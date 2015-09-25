#! /usr/bin/python
# (c) 2015, Adrian Wood <adriangwood@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: gsettings
short_description: Module to manage dconf settings.
description:
    - This module provides a python equivalent of the gsettings command-line tool
    - for use in ansible.
version_added: "1.9"
notes:
    - become and become_user must be used to specify the target configuration set.
requirements: [ "PyGObject"]
options:
    settings:
        description:
            - A list of schema, key, value triplets to update.
        required: true
        default: None
        version_added: 1.9
author: Adrian Wood (@ag-wood)
'''

EXAMPLES = '''
# Desktop settings for Ubuntu Unity Desktop:
- hosts: all
  gather_facts: no
  tasks:
    - name: Change Desktop configuration
    become: yes
    become_user: janedoe
    gsettings:
      settings:
      # Ensure lock when screensaver activates
      - schema: org.gnome.desktop.screensaver
        key: lock-enabled
        value: true
      # Clean up the 'Dash' search widget
      - schema: com.canonical.Unity.Lenses
        key: disabled-scopes
        value: ['more_suggestions-amazon.scope', 'more_suggestions-u1ms.scope',
                'more_suggestions-populartracks.scope', 'music-musicstore.scope',
                'more_suggestions-ebay.scope', 'more_suggestions-ubuntushop.scope',
                'more_suggestions-skimlinks.scope']
      # Power button invokes shutdown.
      - schema: org.gnome.settings-daemon.plugins.power
        key: button-power
        value: 'shutdown'
      # Desktop colours and wallpaper.
      - schema: org.gnome.desktop.background
        key: primary-color
        value: '#000000'
      - schema: org.gnome.desktop.background
        key: secondary-color
        value: '#000000'
      - schema: org.gnome.desktop.background
        key: picture-uri
        value: 'file:///usr/share/backgrounds/Beach_by_Renato_Giordanelli.jpg'
'''

# Standard python libraries
import os
import pwd
import time
import signal
import subprocess

try:
    from gi.repository import Gio, GLib
except ImportError:
    HAS_GIO = False
else:
    HAS_GIO = True

UID = 1
ENV_DBUS = 'DBUS_SESSION_BUS_ADDRESS'
SESSION_MANAGERS = [
    'gnome-session', 'mate-session', 'xfce4-session',
    'cinnamon-session', 'icewm-session', 'openbox-session']

change_msgs = []


class Gsettings(object):
    def __init__(self):
        def proc_name(pid):
            '''Return process name of process pid'''
            try:
                for attr in open("/proc/{0}/status".format(pid), 'r'):
                    if 'Name' in attr:
                        return attr.split(':')[1].strip()
                return None
            except:
                return None

        def proc_owner(pid):
            '''Return username of UID of process pid'''
            for ln in open('/proc/{0}/status'.format(pid), 'r'):
                if ln.startswith('Uid:'):
                    uid = int(ln.split()[UID])
                    return pwd.getpwuid(uid).pw_name

        def proc_environ(pid, varname=""):
            '''Return process environment variable of process pid'''
            proc_env = open("/proc/{0}/environ".format(pid), 'r')
            env = proc_env.readline()
            for env_var in env.split('\x00'):
                if varname != "":
                    prefix = "{0}=".format(varname)
                    if env_var.startswith(prefix):
                        return env_var.replace(prefix, '')

        def bus_address(pid):
            '''Return the bus address for the specified process'''
            return proc_environ(pid, ENV_DBUS)

        def new_session():
            '''Create a new dbus session'''
            dbus_process = subprocess.Popen('dbus-launch', stdout=subprocess.PIPE)
            for env_string in dbus_process.stdout.readlines():
                env_var = env_string[0:env_string.index('=')]
                env_value = env_string[env_string.index('=')+1:]
                os.environ[env_var] = env_value
            return os.environ['DBUS_SESSION_BUS_PID'].replace('\n', '')

        # Determine the session pid.
        self.session_pid = -1
        self._cleanup = False

        for procfile in os.listdir('/proc'):
            if procfile.isdigit():
                if proc_name(procfile) in SESSION_MANAGERS:
                    self.session_pid = procfile

        osuser = pwd.getpwuid(os.getuid()).pw_name
        if self.session_pid != -1:
            sessionuser = proc_owner(self.session_pid)
        else:
            sessionuser = osuser

        # Setup the environment and connect to the bus.
        if self.session_pid != -1 and osuser == sessionuser:
            os.environ[ENV_DBUS] = bus_address(self.session_pid)
        else:
            self.session_pid = new_session()
            self._cleanup = True

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def get_value(self, schema, key):
        ''' Read the value of the specified key '''
        if ':' in schema:
            path = schema.split(':')[1]
            schema = schema.split(':')[0]
        else:
            path = None
        self.settings = Gio.Settings(schema=schema, path=path)
        return self.settings.get_value(key)

    def set_value(self, schema, key, value):
        ''' Write a value to the specified key '''
        if ':' in schema:
            path = schema.split(':')[1]
            schema = schema.split(':')[0]
        else:
            path = None
        self.settings = Gio.Settings(schema=schema, path=path)
        self.settings.set_value(key, value)

    def __del__(self):
        ''' Class cleanup '''
        if self._cleanup:
            # Stop the dbus daemon if one was started.
            time.sleep(1)
            os.kill(int(self.session_pid), signal.SIGTERM)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            settings=dict(required=True, type='list')
        ),
        supports_check_mode=True,
    )

    has_changed = False
    if not HAS_GIO:
        module.fail_json(rc=1, msg='The python PyGObject module is a required dependency.')

    gs = Gsettings()
    gsettings = module.params['settings']
    for gsetting in gsettings:
        schema = gsetting['schema']
        key = gsetting['key']
        value = gsetting['value']
        old_setting = gs.get_value(schema, key)
        setting_type = old_setting.get_type_string()

        old_value = old_setting.unpack()
        if old_value != value:
            has_changed = True
            if module.check_mode:
                change_msgs.append('{0}/{1} -> current: {2} ; proposed new: {3}'.format(schema, key, old_value, value))
            else:
                new_value = GLib.Variant(setting_type, value)
                gs.set_value(schema, key, new_value)
                change_msgs.append('{0}/{1} -> old: {2} ; new: {3}'.format(schema, key, old_value, value))
    gs = None
    module.exit_json(rc=0, changed=has_changed, msg=change_msgs)

from ansible.module_utils.basic import *
if __name__ == '__main__':
    main()
