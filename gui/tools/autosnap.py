#!/usr/bin/env python
#- 
# Copyright (c) 2011 iXsystems, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#

import sys
sys.path.append('/usr/local/www')
sys.path.append('/usr/local/www/freenasUI')

from freenasUI import settings

from django.core.management import setup_environ
setup_environ(settings)

import re
import syslog
from freenasUI.storage.models import Task, Replication
from datetime import datetime, time, timedelta
from shlex import split as shlex_split
from subprocess import Popen, PIPE
from os import system as __system

def pipeopen(command):
    syslog.openlog("autosnap", syslog.LOG_CONS | syslog.LOG_PID)
    syslog.syslog(syslog.LOG_NOTICE, "Popen()ing: " + command)
    args = shlex_split(command)
    return Popen(args, stdin = PIPE, stdout = PIPE, stderr = PIPE, close_fds = True)

def system(command):
    syslog.openlog("autosnap", syslog.LOG_CONS | syslog.LOG_PID)
    syslog.syslog(syslog.LOG_NOTICE, "Executing: " + command)
    __system("(" + command + ") 2>&1 | logger -p daemon.notice -t freenas")
    syslog.syslog(syslog.LOG_INFO, "Executed: " + command)

def snapinfodict2datetime(snapinfo):
    year = int(snapinfo['year'])
    month = int(snapinfo['month'])
    day = int(snapinfo['day'])
    hour = int(snapinfo['hour'])
    minute = int(snapinfo['minute'])
    return datetime(year, month, day, hour, minute)

def snap_expired(snapinfo, snaptime):
    snapinfo_expirationtime = snapinfodict2datetime(snapinfo)
    snap_ttl_value = int(snapinfo['retcount'])
    snap_ttl_unit = snapinfo['retunit']

    if snap_ttl_unit == 'h':
        snapinfo_expirationtime = snapinfo_expirationtime + timedelta(hours = snap_ttl_value)
    elif snap_ttl_unit == 'd':
        snapinfo_expirationtime = snapinfo_expirationtime + timedelta(days = snap_ttl_value)
    elif snap_ttl_unit == 'w':
        snapinfo_expirationtime = snapinfo_expirationtime + timedelta(days = 7*snap_ttl_value)
    elif snap_ttl_unit == 'm':
        if snapinfo_expirationtime == 12:
            snapinfo_expirationtime = snapinfo_expirationtime.replace(year = snapinfo_expirationtime.year+1, month=1)
        else:
            snapinfo_expirationtime = snapinfo_expirationtime.replace(month = snapinfo_expirationtime.month+1)
    elif snap_ttl_unit == 'y':
        snapinfo_expirationtime = snapinfo_expirationtime.replace(year = snapinfo_expirationtime.year+1)

    return snapinfo_expirationtime <= snaptime

def isTimeBetween(time_to_test, begin_time, end_time):
    return ((begin_time <= time_to_test) and (time_to_test <= end_time))

def isMatchingTime(task, snaptime):
    curtime = time(snaptime.hour, snaptime.minute)
    repeat_type = task.task_repeat_unit

    if not isTimeBetween(curtime, task.task_begin, task.task_end):
        return False

    if repeat_type == 'daily':
        return True

    if repeat_type == 'weekly':
        cur_weekday = snaptime.weekday() + 1
        if ('%d' % cur_weekday) in eval(task.task_byweekday):
            return True

    return False

now = datetime.now().replace(microsecond = 0)
if now.second < 30:
    snaptime = now.replace(second = 0)
else:
    snaptime = now.replace(minute = now.minute  + 1, second = 0)

mp_to_task_map = {}

# Grab all matching tasks into a tree.
# Since the snapshot we make have the name 'foo@auto-%Y%m%d.%H%M-{expire time}'
# format, we just keep one task.
TaskObjects = Task.objects.all()
for task in TaskObjects:
    if isMatchingTime(task, snaptime):
        mp_path = task.task_mountpoint.mp_path.__str__()
        expire_time = ('%s%s' % (task.task_ret_count, task.task_ret_unit[0])).__str__()
        tasklist = []
        if mp_to_task_map.has_key((mp_path, expire_time)):
            tasklist = mp_to_task_map[(mp_path, expire_time)]
            tasklist.append(task)
        else:
            tasklist = [task]
        mp_to_task_map[(mp_path, expire_time)] = tasklist

# Do not proceed further if we are not going to generate any snapshots for this run
if len(mp_to_task_map) == 0:
    exit()

# Grab all existing snapshot and filter out the expiring ones
snapshots = {}
snapshots_pending_delete = set()
zfsproc = pipeopen("/sbin/zfs list -t snapshot -H")
lines = zfsproc.communicate()[0].split('\n')
reg_autosnap = re.compile('^auto-(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2}).(?P<hour>\d{2})(?P<minute>\d{2})-(?P<retcount>\d+)(?P<retunit>[hdwmy])$')
for line in lines:
    if line != '':
        snapshot_name = line.split('\t')[0]
        mp, snapname = snapshot_name.split('@')
        mp = '/mnt/' + mp
        snapname_match = reg_autosnap.match(snapname)
        if snapname_match != None:
            snap_infodict = snapname_match.groupdict()
            snap_ret_policy = '%s%s' % (snap_infodict['retcount'], snap_infodict['retunit'])
            if snap_expired(snap_infodict, snaptime):
                snapshots_pending_delete.add(snapshot_name)
            else:
                if mp_to_task_map.has_key((mp, snap_ret_policy)):
                    if snapshots.has_key((mp, snap_ret_policy)):
                        last_snapinfo = snapshots[(mp, snap_ret_policy)]
                        if snapinfodict2datetime(last_snapinfo) < snapinfodict2datetime(snap_infodict):
                            snapshots[(mp, snap_ret_policy)] = snap_infodict
                    else:
                        snapshots[(mp, snap_ret_policy)] = snap_infodict

list_mp = mp_to_task_map.keys()

for mpkey in list_mp:
    tasklist = mp_to_task_map[mpkey]
    if snapshots.has_key(mpkey):
        snapshot_time = snapinfodict2datetime(snapshots[mpkey])
        for taskindex in range(len(tasklist) -1, -1, -1):
            task = tasklist[taskindex]
            if snapshot_time + timedelta(minutes = task.task_interval) > snaptime:
                del tasklist[taskindex]
        if len(tasklist) == 0:
            del mp_to_task_map[mpkey]

snaptime_str = snaptime.strftime('%Y%m%d.%H%M')
sshcmd = '/usr/bin/ssh -i /data/ssh/replication -o BatchMode=yes -o StrictHostKeyChecking=yes -q'

for mpkey in mp_to_task_map:
    mp_path, expire = mpkey
    recursive = False
    for task in tasklist:
        if task.task_recursive == True:
            recursive = True
    if recursive == True:
        recursive = ' -r'
    else:
        recursive = ''

    snapname = '%s@auto-%s-%s' % (mp_path[5:], snaptime_str, expire)

    snapcmd = '/sbin/zfs snapshot%s %s' % (recursive, snapname)
    system(snapcmd)
    """Replicate snapshot to remote system"""
    replication_tasks = Replication.objects.filter(repl_mountpoint__mp_path = mp_path)
    for replication in replication_tasks:
        remote = replication.repl_remote.ssh_remote_hostname.__str__()
        fs = replication.repl_zfs.__str__()
        last_snapshot = replication.repl_lastsnapshot.__str__()
        if last_snapshot == '':
             replcmd = '%s %s /sbin/zfs create -p %s' % (sshcmd, remote, fs)
             system(replcmd)
             replcmd = '/sbin/zfs send %s | %s %s /sbin/zfs receive -F -d %s' % (snapname, sshcmd, remote, fs)
        else:
             replcmd = '/sbin/zfs send -i %s %s | %s %s /sbin/zfs receive -F -d %s' % (last_snapshot, snapname, sshcmd, remote, fs)
        system(replcmd)
        replication.repl_lastsnapshot = snapname
        replication.save()

for snapshot in snapshots_pending_delete:
    snapcmd = '/sbin/zfs destroy %s' % (snapshot)
    system(snapcmd)
