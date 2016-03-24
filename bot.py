#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

from redmine import Redmine
from redmine.exceptions import ResourceAttrError
from pprint import pprint
from types import SimpleNamespace
import datetime
import settings
import telebot
import re

bot = telebot.TeleBot(settings.TELEGRAM_TOKEN)

#
# The workflow used is very simple.
#
# The tickets begin in NEW state and without asignee
# - If someone asignates himself, status changes to IN_PROGRESS with asignee
# - If someone leaves the ticket, status maintains to IN_PROGRESS without asignee
# - If someone closes the ticket, status changes to CLOSED
#


#
# Some constants to put in another place (maybe in settings)
#

class STATUS:
    NEW = 1
    IN_PROGRESS = 2
    CLOSED = 5
    OPEN = 'open'  # NEW + IN_PROGRESS


class USERS:
    ME = 'me'
    NOBODY = None


#
# The key of this dictionary is the chatid and is needed to remember the last
# command that a user wrote. This way, we have a context to understand sentences
# without /slash and apply them to the last command.
#

class TelegramStates:
    def __init__(self):
        self.states = {}

    def set(self, key, **kwargs):
        if key not in self.states:
            self.states[key] = kwargs
        else:
            self.states[key].update(kwargs)

    def get(self, key):
        return self.states.get(key)

    def clean(self, key):
        if key in self.states:
            del self.states[key]


telegram_states = TelegramStates()

#
# Main functions
#


def _generic_tickets(user, what, **filter_args):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    textos = []
    for issue in redmine.issue.filter(**filter_args):
        kwargs = dict(id=issue.id, project=issue.project, subject=issue.subject)
        msg = "/ticket_{id} ({project}) {subject}".format(**kwargs)
        textos.append(msg)
    if len(textos) > 0:
        msg = "Estos son los {}:\n".format(what) + "\n".join(textos)
    else:
        msg = "No tiene {}".format(what)
    return msg


def tickets_nobody_new(user):
    return _generic_tickets(
        user, "tickets nuevos sin coger",
        assigned_to_id=USERS.NOBODY, status_id=STATUS.NEW)


def tickets_nobody_in_progress(user):
    return _generic_tickets(
        user, "tickets en curso sin coger",
        assigned_to_id=USERS.NOBODY, status_id=STATUS.IN_PROGRESS)


def tickets_me_open(user):
    return _generic_tickets(
        user, "tickets abiertos cogidos por mi",
        assigned_to_id=user.id, status_id=STATUS.OPEN)


def tickets_open(user):
    return _generic_tickets(
        user, "tickets abiertos",
        status_id=STATUS.OPEN)


def tickets_in_progress(user):
    return _generic_tickets(
        user, "tickets en curso",
        status_id=STATUS.IN_PROGRESS)


def ticket_info(user, ticket_id):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    issue = redmine.issue.get(ticket_id)
    msg = "Ticket {}:\n".format(ticket_id)
    msg += "- fecha: {}\n".format(issue.created_on)
    msg += "- proyecto: {}\n".format(issue.project)
    if issue.status.id != STATUS.CLOSED:
        msg += "- estado: {} /cierra_{}\n".format(issue.status, ticket_id)
    else:
        msg += "- estado: {}\n".format(issue.status)
    msg += "- asunto: {}\n".format(issue.subject)
    msg += "- descripción: {}\n".format(issue.description)
    msg += "- url: {}issues/{}\n".format(settings.REDMINE_PUBLIC_URL, ticket_id)
    try:
        if issue.assigned_to.id == user.id:
            msg += "- cogido por mi /suelta_{}\n".format(ticket_id)
        else:
            msg += "- cogido por: {} /coge_{}\n".format(issue.assigned_to, ticket_id)
    except ResourceAttrError:
        msg += "- sin coger /coge_{}\n".format(ticket_id)
    return msg.strip()


def open_ticket(user, ticket_id):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    issue = redmine.issue.get(ticket_id)
    if issue.status.id == STATUS.NEW:
        return "El /ticket_{} ya esta abierto!".format(ticket_id)
    else:
        redmine.issue.update(ticket_id, status_id=STATUS.NEW)
        return "Acabas de abrir el /ticket_{}: {}".format(ticket_id, issue.subject)


def ticket_assign(user, ticket_id):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    issue = redmine.issue.get(ticket_id)
    try:
        if issue.assigned_to.id == user.id:
            return "El /ticket_{} ya es tuyo!".format(ticket_id)
    except ResourceAttrError:
        pass  # sin coger
    redmine.issue.update(ticket_id, assigned_to_id=user.id, status_id=STATUS.IN_PROGRESS)
    return "Acabas de cogerte el /ticket_{}: {}".format(ticket_id, issue.subject)


def ticket_forget(user, ticket_id):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    issue = redmine.issue.get(ticket_id)
    try:
        if issue.assigned_to.id == 0:
            return "El /ticket_{} ya esta suelto!".format(ticket_id)
        elif issue.assigned_to.id == user.id:
            redmine.issue.update(ticket_id, assigned_to_id=0)
            return "Acabas de soltar el /ticket_{}: {}".format(ticket_id, issue.subject)
        else:
            return "El /ticket_{} no es tuyo!".format(ticket_id)
    except ResourceAttrError:
        return "El /ticket_{} ya esta suelto!".format(ticket_id)


def ticket_close(user, ticket_id):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    issue = redmine.issue.get(ticket_id)
    if issue.status.id == STATUS.CLOSED:
        return "El /ticket_{} ya esta cerrado!".format(ticket_id)
    else:
        redmine.issue.update(ticket_id, status_id=STATUS.CLOSED)
        return "Acabas de cerrar el /ticket_{}: {}".format(ticket_id, issue.subject)


def ticket_note(user, ticket_id, mensaje):
    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    redmine.issue.update(ticket_id, notes=mensaje)
    return "ok"


def ticket_note_with_time(user, ticket_id, mensaje, minutes):
    if isinstance(minutes, str):
        if minutes[-1] in "hH":
            hours = int(minutes[:-1])
        elif minutes[-1] in "mM":
            hours = float(minutes[:-1])/60.
        else:
            hours = float(minutes)/60.
    else:
        hours = minutes/60.

    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY, impersonate=user.login)
    redmine.issue.update(ticket_id, notes=mensaje)
    redmine.time_entry.create(issue_id=ticket_id, hours=hours,
                              activity_id=settings.DEFAULT_ACTIVITY, comments=mensaje)
    return "Anotado que has trabajado {minutes} minutos en el /ticket_{i}.\n" \
        "Si quieres /suelta_{i} o /cierra_{i} o sigue trabajando".format(
            minutes=int(hours*60), i=ticket_id)

#
# Commands for each previous function
#

EXPRESIONES = (
    (r'/nuevos', tickets_nobody_new),
    (r'/abandonados', tickets_nobody_in_progress),
    (r'/mios', tickets_me_open),
    (r'/abiertos', tickets_open),
    (r'/encurso', tickets_in_progress),
    (r'/ticket_(?P<ticket_id>\d+)', ticket_info),
    (r'/abre_(?P<ticket_id>\d+)', open_ticket),
    (r'/coge_(?P<ticket_id>\d+)', ticket_assign),
    (r'/suelta_(?P<ticket_id>\d+)', ticket_forget),
    (r'/cierra_(?P<ticket_id>\d+)', ticket_close),
    (r'/?(nota[ _])?(?P<ticket_id>\d+) (?P<mensaje>.*) (?P<minutes>\d+[hHmM]?)', ticket_note_with_time),
    (r'/?(nota[ _])?(?P<ticket_id>\d+) (?P<mensaje>.*)', ticket_note),
)


#
# Authorized telegram users from Redmine
# (in redmine we have a custom field for user called "telegram" type integer
# with the telegram id of each user)
#

def get_telegram_users():
    "Fills dictionary from redmine with objects for each authorized user"

    redmine = Redmine(settings.REDMINE_API_URL, key=settings.REDMINE_KEY)
    # We search all users from redmine that have a telegram id and we build
    # a list of objects with useful data
    out = {}
    for user in redmine.user.all():
        for cf in user.custom_fields:
            if cf.name == "telegram":
                chatid = int(cf.value)
                if chatid > 0:
                    out[chatid] = SimpleNamespace(
                        name="{} {}".format(user.firstname, user.lastname),
                        login=user.login,
                        id=user.id,
                    )
                    break
    return out

TELEGRAM_USERS = get_telegram_users()
print("Usuarios de telegram-redmine:")
pprint(TELEGRAM_USERS)


#
# Decorator for authorization
#

def _only_authorized(f):
    def func(message):
        if message.from_user.username:
            username = "@{}".format(message.from_user.username)
        else:
            username = "{} {}".format(message.from_user.first_name, message.from_user.last_name)
        date = datetime.datetime.utcfromtimestamp(message.date)
        print("[{}] {} (#{}) escribió '{}'".format(
            date, username, message.from_user.id, message.text))

        chatid = message.from_user.id
        if chatid not in TELEGRAM_USERS:
            bot.reply_to(message, "No te conozco {}".format(chatid))
            return
        f(message)
    return func


#
# Bot commands
#

@bot.message_handler(commands=['start'])
def command_start(message):
    msg = "Esto es un bot para interactuar con el Redmine de {}\n".format(settings.COMPANY)
    msg += "Pica en /help para tener mas información\n"
    bot.reply_to(message, msg)


@bot.message_handler(commands=['help', '/help'])
@_only_authorized
def command_help(message):
    msg = "Los siguientes comandos estan disponibles:\n"
    msg += "/nuevos tickets nuevos sin coger\n"
    msg += "/abandonados tickets en curso sin coger\n"
    msg += "/mios tickets abiertos cogidos por mi\n"
    msg += "/abiertos tickets abiertos\n"
    msg += "/encurso tickets en curso\n"
    bot.reply_to(message, msg)


@bot.message_handler(commands=['tickets'])
@bot.message_handler(func=lambda message: True)  # Pilla todos los comandos
@_only_authorized
def command_all(message):
    "catch-all command that calls simplified functions with argument 'user' and tokens"
    chatid = message.from_user.id
    txt = message.text.strip()
    user = TELEGRAM_USERS[chatid]
    msg = "No entiendo"
    for regex, func in EXPRESIONES:
        m = re.fullmatch(regex, txt)
        if m:
            d = m.groupdict()
            # arguments are: user and a dictionary with named items of the regexp
            msg = func(user, **d)
            if 'ticket_id' in d:
                telegram_states.set(chatid, date=message.date, **d)
            else:
                telegram_states.clean(chatid)
            break
    else:
        if message.chat.type == "private":
            old = telegram_states.get(chatid)
            if old and 'ticket_id' in old:
                minutes = (message.date - old['date'])//60
                msg = ticket_note_with_time(user, old['ticket_id'], txt, minutes)
                telegram_states.set(chatid, date=message.date)

    msg = msg.strip()
    print("----------Se le respondio------\n{}\n-----------".format(msg))
    if message.chat.type == "private":
        bot.send_message(chatid, msg)
    else:
        bot.reply_to(message, msg)


print("main loop polling...")
bot.polling()

# To remember:
#
# issue attributes:
# ['attachments', 'author', 'changesets', 'children', 'created_on', 'description',
# 'done_ratio', 'id', 'journals', 'priority', 'project', 'relations', 'start_date',
# 'status', 'subject', 'time_entries', 'tracker', 'updated_on', 'watchers']
#
# user attributes:
# ['contacts', 'deals', 'groups', 'id', 'issues', 'memberships', 'name', 'time_entries']
