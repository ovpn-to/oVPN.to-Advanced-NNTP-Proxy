#!/usr/bin/python2 -O
#
#   oVPN.to Advanced NNTP Proxy - Version: 0.4.27 (PUBLIC)
#
#   Thanks to people for ideas:
#       + ddeus@sourceforge (NNTP2NNTP)
#       + pjenvey/hellanzb@github
#
#   Copyright:
#       oVPN.to Anonymous Services
#
#   License: Free to use and modify, just keep same License and add yourself to Copyright!
#
###
"""
    Some infos and notes:
    
    + Requirements:
        OS: Any with python 2.7 and libmysqlclient-dev
        RAM: 512MB - 1 GB should be fine
        CPU: Xeon E3-1240v3 (4c/8t): all threads @ 100% while doing 400-450 Mbps I/O
                with 1 config per core and haproxy to distribute connections among processes.
    
   + mysql: user auth with connlimit and expiration. password hashs = sha256.
   + mysql: user and backend traffic stats and established backends/sessions.
   + supports multiple providers/accounts. set equal provider accs to same `bgrp` and set different `name`.
   + if article not found: 1 user-connection establishs 1 connection to every backend (but only 1 in same `bgrp`) while searching!
   + DEBUG_LEVEL: 1 - 4 is almost silent, 5 will show some usefull info and 6 - 9 will spam lots of debugs.
   + telnet admin interface: set adminpwd in config file and connect 'telnet localhost 11119'
        telnet commands are 1-liner like: ADMIN AUTH myPASSWORD $COMMAND $VALUE
        
        # change debug level
        ~: ADMIN AUTH myPASSWORD DEBUG 5
        
        # show some information
        ~: ADMIN AUTH myPASSWORD INFO
        
        # close the process
        ~: ADMIN AUTH myPASSWORD CLOSE
        
        # re-open the process
        ~: ADMIN AUTH myPASSWORD OPEN
        
        # shutdown the process
        ~: ADMIN AUTH myPASSWORD SHUTDOWN
        
        # print debug values to logfile
        ~: ADMIN AUTH myPASSWORD PRINT
    
    + you should search for '# hack to select provider on first connect' and set tback
    
    + to be continued...
"""

def sample_config():
    sys.stdout.write("\nExample nntp.conf:\n\n")
    sys.stdout.write("""
[frontend]
bindhost = 127.0.0.1            # set listen address
bindport = 11119                # set listen port
cfg_read = 300                  # reload config every x seconds
logs = False                    # enable logfile
logf = nntp.log                 # logfile path
adminpwd = None                 # set telnet admin password
nodeid = 1                      # set physical nodeid

[caching]
max_notf_cache = 50             # max cache for not found articles
max_notf_etime = 10800          # expire not found articles after x seconds
flush_cache_every = 10          # run cache_thread every x seconds

[userdb_mysql]
userdb_name = nntp_users
userdb_host = 127.0.0.1
userdb_user = root
userdb_pass = None

[backdb_mysql]
backdb_name = nntp_backends
backdb_host = 127.0.0.1
backdb_user = root
backdb_pass = None

[sessdb_mysql]
sessdb_name = nntp_sessions
sessdb_host = 127.0.0.1
sessdb_user = root
sessdb_pass = None

    """)
    
    sys.stdout.write("\nExample SQL USERS Datanbase:\n\n")
    sys.stdout.write("""
CREATE DATABASE IF NOT EXISTS `nntp_users` DEFAULT CHARACTER SET utf8 COLLATE utf8_bin;
USE `nntp_users`;

DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
  `name` char(16) COLLATE utf8_bin NOT NULL,
  `maxconns` int(11) NOT NULL,
  `established` int(11) NOT NULL DEFAULT '0',
  `stoptime` int(11) NOT NULL,
  `passwd` char(64) COLLATE utf8_bin NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_bin;

ALTER TABLE `users`
  ADD UNIQUE KEY `name` (`name`);

ALTER TABLE `users` ADD `rxbytes` BIGINT(22) NOT NULL DEFAULT '0' AFTER `passwd`, ADD `rxlines` BIGINT(22) NOT NULL DEFAULT '0' AFTER `rxbytes`, ADD `notfound` BIGINT(22) NOT NULL DEFAULT '0' AFTER `rxlines`, ADD `duration` BIGINT(22) NOT NULL DEFAULT '0' AFTER `notfound`;
ALTER TABLE `users` ADD `articles` BIGINT(22) NOT NULL DEFAULT '0' AFTER `duration`;
ALTER TABLE `users` ADD `jumps` BIGINT(22) NOT NULL DEFAULT '0' AFTER `articles`;

REVOKE ALL PRIVILEGES ON `nntp_users`.`users` FROM 'nntp'@'%'; 
GRANT SELECT, INSERT (`established`, `rxbytes`, `rxlines`, `notfound`, `duration`, `articles`, `jumps`), UPDATE (`established`, `rxbytes`, `rxlines`, `notfound`, `duration`, `articles`, `jumps`) ON `nntp_users`.`users` TO 'nntp'@'%';

INSERT INTO `users` (`name`, `maxconns`, `stoptime`, `passwd`) VALUES
('ovpn1', 4, 0, 'ad95d5fa651ba86d8923fe1238d24a4f1988a752acfe426ac72ac7c04471bc17'),
('ovpn12345', 4, 2147483647, 'ad95d5fa651ba86d8923fe1238d24a4f1988a752acfe426ac72ac7c04471bc17');
    """)
    
    sys.stdout.write("\nExample SQL BACKENDS Datanbase:\n\n")
    sys.stdout.write("""
CREATE DATABASE IF NOT EXISTS `nntp_backends` DEFAULT CHARACTER SET utf8 COLLATE utf8_bin;
USE `nntp_backends`;
DROP TABLE IF EXISTS `backends`;
CREATE TABLE `backends` (
  `bid` int(11) NOT NULL,
  `bgrp` char(16) COLLATE utf8_bin NOT NULL,
  `name` char(16) COLLATE utf8_bin NOT NULL,
  `host` char(64) COLLATE utf8_bin NOT NULL,
  `port` int(11) NOT NULL,
  `user` char(64) COLLATE utf8_bin NOT NULL,
  `passwd` char(64) COLLATE utf8_bin NOT NULL,
  `conns` int(11) NOT NULL,
  `tout` int(11) NOT NULL DEFAULT '3',
  `expire` int(11) NOT NULL DEFAULT '0',
  `expdate` char(16) COLLATE utf8_bin NOT NULL DEFAULT '2016-12-31',
  `enable` int(11) NOT NULL DEFAULT '0',
  `provider` char(32) COLLATE utf8_bin NOT NULL,
  `retention` int(11) NOT NULL DEFAULT '-1',
  `priority` int(11) NOT NULL DEFAULT '-1',
  `price` char(8) COLLATE utf8_bin NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_bin;

    GRANT SELECT ON `nntp\_backends`.* TO 'nntp'@'%';
    """)
    
    sys.stdout.write("\nExample SQL SESSIONS Datanbase:\n\n")
    sys.stdout.write("""
CREATE DATABASE IF NOT EXISTS `nntp_sessions` DEFAULT CHARACTER SET utf8 COLLATE utf8_bin;
USE `nntp_sessions`;

DROP TABLE IF EXISTS `sessions`;
CREATE TABLE `sessions` (
  `id` bigint(22) NOT NULL,
  `sessionid` char(32) COLLATE utf8_bin NOT NULL,
  `backendid` int(11) NOT NULL,
  `conntime` int(11) NOT NULL,
  `username` char(32) COLLATE utf8_bin NOT NULL,
  `node` char(16) COLLATE utf8_bin NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_bin;

ALTER TABLE `sessions`
  ADD PRIMARY KEY (`id`);

ALTER TABLE `sessions`
  MODIFY `id` bigint(22) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=1;
  
GRANT SELECT, INSERT, UPDATE, DELETE ON `nntp\_sessions`.* TO 'nntp'@'%';
    """)
    
    sys.stdout.write("\nExample SQL BACKEND STATS Datanbase:\n\n")
    sys.stdout.write("""
CREATE DATABASE IF NOT EXISTS `nntp_stats` DEFAULT CHARACTER SET utf8 COLLATE utf8_bin;
USE `nntp_stats`;

DROP TABLE IF EXISTS `bestats`;
CREATE TABLE `bestats` (
  `bid` int(11) NOT NULL,
  `rxbytes` bigint(22) NOT NULL DEFAULT '0',
  `txbytes` bigint(22) NOT NULL DEFAULT '0',
  `article` bigint(22) NOT NULL DEFAULT '0',
  `nofound` bigint(22) NOT NULL DEFAULT '0',
  `choosen` bigint(22) NOT NULL DEFAULT '0',
  `failure` bigint(22) NOT NULL DEFAULT '0',
  `updated` int(11) NOT NULL DEFAULT '0'
) ENGINE=InnoDB DEFAULT CHARSET=utf8 COLLATE=utf8_bin;


ALTER TABLE `bestats` ADD PRIMARY KEY (`bid`);
ALTER TABLE `bestats` ADD UNIQUE(`bid`);

GRANT SELECT, INSERT, UPDATE ON `nntp\_stats`.* TO 'nntp'@'%';
    """)
    
    sys.exit(1)

import binascii, hashlib, os, psutil, random, sys, time, threading, zlib, gc, json
from twisted.internet import defer
from twisted.enterprise import adbapi
from twisted.internet.protocol import ServerFactory, ClientFactory, Factory
from twisted.python import log
from twisted.news.nntp import NNTPClient, NNTPServer, extractCode
from ConfigParser import SafeConfigParser
Factory.noisy = False

#GLOBALS: VARS
SERVER_CLOSE_FILE = './nntp.close'
MSG_WELCOME = "201 nntp.ovpn.to"
MSG_LOGIN = "281 Welcome to oVPN.to!"
ALLOW_CMDS = ( 'ARTICLE', 'BODY' )          # supports ARTICLE, BODY, HEAD

# GLOBALS: INT
DEBUG_LEVEL = 5
RELOAD_CONFIG_EVERY = 300                   # RELOAD CONFIG
SESSION_TIMEOUT = 3600                      # disconnect authed session after x seconds
#MAX_NOAUTH_TIME = 9                         # disconnect not authed after x seconds
#MAX_IDLE_TIME = 30                          # disconnect session after x seconds without cmd

# GLOBALS: LIST
CURRENT_BACKEND_CONNS = list()              # to be filled with backends and their conns counter
NEWSGROUPS_LIST = list()                    # to be filled with newsgroups LIST

# GLOBALS: DICT
CONN_ID = dict()                            # stores frontend conn_id infos
FRONTEND_USER_CONNS = dict()                # stores frontend established connections to provide conn limits
DEAD_BACKENDS = dict()                      # to be filled with dead (timeout/authinfofailed) backend names and blocking time
LOGBCONNS = dict()                          # stores backend conns to cleanup
META_CACHE = dict()                         # some global meta cache
META_CACHE["NOTF"] = dict()                 # list with 430 not found articles
TEMPORARY_BLOCKED = dict()                  # stores temp blocked users
LAST_ACTIONS = dict()                       # stores last runtime of internal jobs
CLIENT_FACTS = dict()                       # stores established ClientFactories
DBPOOL = dict()                             # stores mysql dbpools
BESTATS = dict()                            # stores backends stats

# GLOBALS: INITIAL ZERO
CURRENT_TEMP_CONNS = 0
META_CACHE["SENT"] = 0                      # total number of sent articles

# GLOBALS LAST_ACTIONS
LAST_ACTIONS["CFG_RELOAD"] = 0
LAST_ACTIONS["RUN_CACHE_THREAD"] = 0
LAST_ACTIONS["GC_COLLECT"] = 0
LAST_ACTIONS["MYSQL_LOAD_USERS"] = 0
LAST_ACTIONS["MYSQL_LOAD_BACKS"] = 0
LAST_ACTIONS["MYSQL_UPDATE_BESTATS"] = 0
LAST_ACTIONS["MYSQL_CLEAR_BACKEND_SESSIONS"] = 0
LAST_ACTIONS["FORCE_NOTF_EXPIRE"] = 0

# GLOBALS: FALSE
UPDATE_TRAFFICDB_RUNNING = False    # false!
IDLE_TIMER_RUNNING = False          # false!
CACHE_THREAD_RUNNING = False        # false!
FLUSH_CACHE = False                 # false!
CONFIG = False                      # false!

# GLOBALS: TRUE
GLOBAL_SHUTDOWN = True              # True! keep server closed while booting

# GLOBALS: MYSQL DBPOOLS

DBPOOL['USERS'] = None
DBPOOL['BACKS'] = None
DBPOOL['SESSS'] = None
DBPOOL['STATS'] = None

def NOW():
    return time.time()

def clean_REASON(reason):
        strreason = str(reason)
        if "timeout" in strreason:
            reason = "timeout"
        elif "refused" in strreason:
            reason = "refused"
        elif "cleanly" in strreason:
            reason = "cleanly"
        elif "unclean" in strreason:
            reason = "unclean"
        else: 
            reason = "error unknown reason: '%s'" % reason
        return reason

def dbg(lvl,msg):
    if lvl > 0 and lvl <= DEBUG_LEVEL:
        try:
            print(str(time.time())+":  "+msg)
        except Exception as e:
            print("print failed, exception = '%s'"%(e))

def GET_BACKEND_INFO(backendid,type=None,src=None):
    # backendname = GET_BACKEND_INFO(self.server.BEid,'NAME')
    if type in ('NAME','GROUP','BID'):
        try:
            if backendid <= len(CONFIG['BACKENDS'])-1:
                value = CONFIG["BACKENDS"][backendid][type]
                if src != None:
                    dbg(5,"def GET_BACKEND_INFO: bid=%s, type=%s, return value='%s'"%(type,backendid,value))
                return value
        except Exception as e:
            dbg(1,"def GET_BACKEND_INFO: failed, exception = '%s', bid=%s, type=%s"%(e,type,backendid))
    return False

def get_runtime(waittime):
    runtime = round(NOW()-waittime,3)
    return runtime

def read_config():
    global RELOAD_CONFIG_EVERY
    global DBPOOL
    global ADMINPWD
    
    if len(sys.argv) > 1 and len(sys.argv[1]) > 1:
        maincfg = sys.argv[1]
        if os.path.isfile(maincfg):
            dbg(7,"def read_config: reading maincfg '%s'"%(maincfg))
            main_cfg = SafeConfigParser()
            main_cfg.read(maincfg)
            
            # read frontend section
            if main_cfg.has_option('frontend', 'bindhost') \
                and main_cfg.has_option('frontend', 'bindport') \
                and main_cfg.has_option('frontend', 'cfg_read') \
                and main_cfg.has_option('frontend', 'logs') \
                and main_cfg.has_option('frontend', 'logf') \
                and main_cfg.has_option('frontend', 'nodeid') \
                and main_cfg.has_option('frontend', 'adminpwd'):
                
                getADMINPWD = main_cfg.get('frontend', 'adminpwd')
                if getADMINPWD in ('None','False'):
                    ADMINPWD = None
                else:
                    ADMINPWD = getADMINPWD
                
                LHOST = main_cfg.get('frontend', 'bindhost')
                LPORT = main_cfg.getint('frontend', 'bindport')
                RELOAD_CONFIG_EVERY = main_cfg.getint('frontend', 'cfg_read')
                if LPORT <= 1024:
                    LPORT = 11119
                
                FRONTEND = dict()
                FRONTEND["LOGS"] = main_cfg.getboolean('frontend', 'logs')
                FRONTEND["LOGF"] = main_cfg.get('frontend', 'logf')
                NODEID = main_cfg.get('frontend', 'nodeid')
            else:
                print("[frontend] section failed")
                return False
            
            # read userdb_mysql section
            update_sql = False
            TMP = dict()
            TMP["USQL"] = dict()
            TMP["USQL"]["dbname"] = main_cfg.get('userdb_mysql', 'userdb_name')
            TMP["USQL"]["dbhost"] = main_cfg.get('userdb_mysql', 'userdb_host')
            TMP["USQL"]["dbuser"] = main_cfg.get('userdb_mysql', 'userdb_user')
            TMP["USQL"]["dbpass"] = main_cfg.get('userdb_mysql', 'userdb_pass')
            
            if TMP["USQL"]["dbpass"] == 'None':
                TMP["USQL"]["dbpass"] = ''
            
            if CONFIG != False and "FRONTEND" in CONFIG and "USQL" in CONFIG["FRONTEND"]:
                if \
                CONFIG["FRONTEND"]["USQL"]["dbname"] != TMP["USQL"]["dbname"] or \
                CONFIG["FRONTEND"]["USQL"]["dbhost"] != TMP["USQL"]["dbhost"] or \
                CONFIG["FRONTEND"]["USQL"]["dbuser"] != TMP["USQL"]["dbuser"] or \
                CONFIG["FRONTEND"]["USQL"]["dbpass"] != TMP["USQL"]["dbpass"]:
                    update_sql = True
            else:
                update_sql = True
            
            
            if update_sql == True:
                dbg(7,"update_sql USERS, CONFIG = '%s'"%CONFIG)
                dbg(1,"def read_config: update_sql ConnectionPool (USERS)")
                FRONTEND["USQL"] = TMP["USQL"]
                
                try:
                    DBPOOL['USERS'] = adbapi.ConnectionPool("MySQLdb", 
                                        db=FRONTEND["USQL"]["dbname"], 
                                        host=FRONTEND["USQL"]["dbhost"], 
                                        user=FRONTEND["USQL"]["dbuser"], 
                                        passwd=FRONTEND["USQL"]["dbpass"],
                                        cp_reconnect=True
                                        )
                except Exception as e:
                    dbg(1,"CONFIG FAILED: DBPOOL_USERS, exception = '%s'"%e)
            else:
                if "USQL" in CONFIG["FRONTEND"]:
                    FRONTEND["USQL"] = CONFIG["FRONTEND"]["USQL"]
        
            # read backdb_mysql section
            update_sql = False
            TMP = dict()
            TMP["BSQL"] = dict()
            TMP["BSQL"]["dbname"] = main_cfg.get('backdb_mysql', 'backdb_name')
            TMP["BSQL"]["dbhost"] = main_cfg.get('backdb_mysql', 'backdb_host')
            TMP["BSQL"]["dbuser"] = main_cfg.get('backdb_mysql', 'backdb_user')
            TMP["BSQL"]["dbpass"] = main_cfg.get('backdb_mysql', 'backdb_pass')
            
            if TMP["BSQL"]["dbpass"] == 'None':
                TMP["BSQL"]["dbpass"] = ''
            
            if CONFIG != False and "FRONTEND" in CONFIG and "BSQL" in CONFIG["FRONTEND"]:
                if \
                CONFIG["FRONTEND"]["BSQL"]["dbname"] != TMP["BSQL"]["dbname"] or \
                CONFIG["FRONTEND"]["BSQL"]["dbhost"] != TMP["BSQL"]["dbhost"] or \
                CONFIG["FRONTEND"]["BSQL"]["dbuser"] != TMP["BSQL"]["dbuser"] or \
                CONFIG["FRONTEND"]["BSQL"]["dbpass"] != TMP["BSQL"]["dbpass"]:
                    update_sql = True
            else:
                update_sql = True
            
            if update_sql == True:
                dbg(7,"update_sql BACKS, CONFIG = '%s'"%CONFIG)
                dbg(1,"def read_config: update_sql ConnectionPool (BACKS)")
                FRONTEND["BSQL"] = TMP["BSQL"]
                
                try:
                    DBPOOL['BACKS'] = adbapi.ConnectionPool("MySQLdb", 
                                        db=FRONTEND["BSQL"]["dbname"], 
                                        host=FRONTEND["BSQL"]["dbhost"], 
                                        user=FRONTEND["BSQL"]["dbuser"], 
                                        passwd=FRONTEND["BSQL"]["dbpass"],
                                        cp_reconnect=True
                                        )
                except Exception as e:
                    dbg(1,"CONFIG FAILED: DBPOOL_BACKS, exception = '%s'"%e)
            else:
                if "BSQL" in CONFIG["FRONTEND"]:
                    FRONTEND["BSQL"] = CONFIG["FRONTEND"]["BSQL"]
            
            # read sessions_mysql section
            update_sql = False
            TMP = dict()
            TMP["SSQL"] = dict()
            TMP["SSQL"]["dbname"] = main_cfg.get('sessdb_mysql', 'sessdb_name')
            TMP["SSQL"]["dbhost"] = main_cfg.get('sessdb_mysql', 'sessdb_host')
            TMP["SSQL"]["dbuser"] = main_cfg.get('sessdb_mysql', 'sessdb_user')
            TMP["SSQL"]["dbpass"] = main_cfg.get('sessdb_mysql', 'sessdb_pass')
            
            if TMP["SSQL"]["dbpass"] == 'None':
                TMP["SSQL"]["dbpass"] = ''
            
            if CONFIG != False and "FRONTEND" in CONFIG and "SSQL" in CONFIG["FRONTEND"]:
                if \
                CONFIG["FRONTEND"]["SSQL"]["dbname"] != TMP["SSQL"]["dbname"] or \
                CONFIG["FRONTEND"]["SSQL"]["dbhost"] != TMP["SSQL"]["dbhost"] or \
                CONFIG["FRONTEND"]["SSQL"]["dbuser"] != TMP["SSQL"]["dbuser"] or \
                CONFIG["FRONTEND"]["SSQL"]["dbpass"] != TMP["SSQL"]["dbpass"]:
                    update_sql = True
            else:
                update_sql = True
            
            if update_sql == True:
                dbg(7,"update_sql SESSIONS, CONFIG = '%s'"%CONFIG)
                dbg(1,"def read_config: update_sql ConnectionPool (SESSIONS)")
                FRONTEND["SSQL"] = TMP["SSQL"]
                
                try:
                    DBPOOL['SESSS'] = adbapi.ConnectionPool("MySQLdb", 
                                        db=FRONTEND["SSQL"]["dbname"], 
                                        host=FRONTEND["SSQL"]["dbhost"], 
                                        user=FRONTEND["SSQL"]["dbuser"], 
                                        passwd=FRONTEND["SSQL"]["dbpass"],
                                        cp_reconnect=True
                                        )
                except Exception as e:
                    dbg(1,"CONFIG FAILED: DBPOOL_SESSS, exception = '%s'"%e)
            else:
                if "SSQL" in CONFIG["FRONTEND"]:
                    FRONTEND["SSQL"] = CONFIG["FRONTEND"]["SSQL"]
            
            # read stats_mysql section
            update_sql = False
            TMP = dict()
            TMP["STSQL"] = dict()
            TMP["STSQL"]["dbname"] = main_cfg.get('statdb_mysql', 'statdb_name')
            TMP["STSQL"]["dbhost"] = main_cfg.get('statdb_mysql', 'statdb_host')
            TMP["STSQL"]["dbuser"] = main_cfg.get('statdb_mysql', 'statdb_user')
            TMP["STSQL"]["dbpass"] = main_cfg.get('statdb_mysql', 'statdb_pass')
            
            if TMP["STSQL"]["dbpass"] == 'None':
                TMP["STSQL"]["dbpass"] = ''
            
            if CONFIG != False and "FRONTEND" in CONFIG and "STSQL" in CONFIG["FRONTEND"]:
                if \
                CONFIG["FRONTEND"]["STSQL"]["dbname"] != TMP["STSQL"]["dbname"] or \
                CONFIG["FRONTEND"]["STSQL"]["dbhost"] != TMP["STSQL"]["dbhost"] or \
                CONFIG["FRONTEND"]["STSQL"]["dbuser"] != TMP["STSQL"]["dbuser"] or \
                CONFIG["FRONTEND"]["STSQL"]["dbpass"] != TMP["STSQL"]["dbpass"]:
                    update_sql = True
            else:
                update_sql = True
            
            if update_sql == True:
                dbg(7,"update_sql STATS CONFIG = '%s'"%CONFIG)
                dbg(1,"def read_config: update_sql ConnectionPool (STATS)")
                FRONTEND["STSQL"] = TMP["STSQL"]
                
                try:
                    DBPOOL['STATS'] = adbapi.ConnectionPool("MySQLdb", 
                                        db=FRONTEND["STSQL"]["dbname"], 
                                        host=FRONTEND["STSQL"]["dbhost"], 
                                        user=FRONTEND["STSQL"]["dbuser"], 
                                        passwd=FRONTEND["STSQL"]["dbpass"],
                                        cp_reconnect=True
                                        )
                except Exception as e:
                    dbg(1,"CONFIG FAILED: DBPOOL_STATS, exception = '%s'"%e)
            else:
                if "STSQL" in CONFIG["FRONTEND"]:
                    FRONTEND["STSQL"] = CONFIG["FRONTEND"]["STSQL"]
            
            # read caching section
            if main_cfg.has_section('caching'):
                tmpcfg = dict()
                
                options = ( 'max_notf_cache', 'flush_cache_every', 'max_notf_etime')
                
                for opt in options:
                    if main_cfg.has_option('caching', opt):
                        tmpcfg[opt] = main_cfg.getint('caching', opt)
                    else:
                        if opt == 'max_notf_etime':
                            tmpcfg[opt] = 10800
                        elif opt == 'max_notf_cache':
                            tmpcfg[opt] = 5000
                            
                if len(tmpcfg) == len(options):
                    CACHING = tmpcfg
                else:
                    dbg(1,"def read_config: caching failed %d/%d"%(len(tmpcfg),len(options)))
                    return False
            
            # read backends sections
            if not CONFIG == False:
                if "BACKENDS" in CONFIG:
                    BACKENDS = CONFIG["BACKENDS"]
            else:
                BACKENDS = list()
            
            # read users
            if not CONFIG == False:
                if "USERS" in CONFIG:
                    USERS = CONFIG["USERS"]
                
                if "CONNS" in CONFIG:
                    CONNS = CONFIG["CONNS"]
            else:
                USERS = dict()
                CONNS = dict()
        else:
            dbg(1,"configfile not found")
            return False
    else:
        dbg(1,"configfile not defined")
        return False
    dbg(7,"def read_config: loaded backends '%s'"%(BACKENDS))
    return { 'BACKENDS':BACKENDS, 'FRONTEND':FRONTEND, 'CONNS':CONNS, 'CACHING':CACHING, 'LHOST':LHOST, 'LPORT':LPORT, 'USERS':USERS, 'NODEID':NODEID }

def CONFIG_LOAD():
    global CONFIG
    global LAST_ACTIONS
    cfg = read_config()
    if cfg == False:
        LAST_ACTIONS["CFG_RELOAD"] = int(time.time()+30-RELOAD_CONFIG_EVERY)
        dbg(1,"def CONFIG_LOAD(): failed, retry in 30 sec")
    else:
        CONFIG = cfg
        LAST_ACTIONS["CFG_RELOAD"] = int(NOW())
        dbg(2,"def CONFIG_LOAD(): OK")

def print_timing(func):
    def wrapper(*arg):
        t1 = time.clock()
        res = func(*arg)
        t2 = time.clock()
        print '%0.3fms' % ((t2-t1)*1000.0)
        return res
    return wrapper

# MYSQL SET BACKEND SESSIONS

def CALLBACK_SET_BACKEND_SESSION(result, direction, connid):
    dbg(7,"(%s) def CALLBACK_SET_BACKEND_SESSION: result = '%s'"%(connid,result))
    return

def MYSQL_SET_BACKEND_SESSION(connid, beid, name, direction, src = None):
    dbg(7,"(%s) def MYSQL_SET_BACKEND_SESSION: beid=%s (%s) %s, src = '%s'"%(connid, beid, name, direction, src))
    try:
        return DBPOOL['SESSS'].runInteraction(mysql_query_set_backend_session, connid, beid, name, direction)
    except Exception as e:
        dbg(1,"(%s) def MYSQL_SET_BACKEND_SESSION: failed, exception = '%s'"%(connid,e))
        return False

def mysql_query_set_backend_session(txn, connid, beid, name, direction):
    try:
        if name == None:
            return [0, name]
        if not name.isalnum():
            return [0, name]
        if direction == "up":
            result = txn.execute("INSERT INTO `sessions` ( sessionid, backendid, conntime, username, node) VALUES ('%s','%s','%s','%s','%s:%s')"%(connid,beid,int(NOW()),name,CONFIG['NODEID'],CONFIG['LPORT']))
        if direction == "down":
            result = txn.execute("DELETE FROM `sessions` WHERE sessionid = '%s' AND backendid = '%s' AND node = '%s:%s'"%(connid,beid,CONFIG['NODEID'],CONFIG['LPORT']))
        if result:
            #print result
            return [result,name]
        else:
            return None
    except Exception as e:
        dbg(1,"(%s) def mysql_query_set_backend_session: failed, exception = '%s'"%(connid,e))
        return False

# MYSQL CLEAR BACKEND SESSIONS

def CALLBACK_CLEAR_BACKEND_SESSIONS(result):
    if result != None:
        dbg(5,"def CALLBACK_CLEAR_BACKEND_SESSIONS: result = '%s'"%(result))
    return

def MYSQL_CLEAR_BACKEND_SESSIONS():
    dbg(7,"def MYSQL_CLEAR_BACKEND_SESSIONS()")
    try:
        return DBPOOL['SESSS'].runInteraction(mysql_query_clear_backend_sessions)
    except Exception as e:
        dbg(1,"(%s) def MYSQL_CLEAR_BACKEND_SESSIONS: failed, exception = '%s'"%(e))
        return False

def mysql_query_clear_backend_sessions(txn):
    try:
        result = txn.execute("DELETE FROM `sessions` WHERE node = '%s:%s'"%(CONFIG['NODEID'],CONFIG['LPORT']))
        if result:
            return result
        else:
            return None
    except Exception as e:
        dbg(1,"def mysql_query_clear_backend_sessions: failed, exception = '%s'"%(e))
        return False

# MYSQL CREATE BESTATS

def CALLBACK_CREATE_BESTATS(result, bid):
    if result != None:
        dbg(5,"def CALLBACK_CREATE_BESTATS: bid='%s', result = '%s'"%(bid,result))
    return

def MYSQL_CREATE_BESTATS(bid):
    dbg(7,"def MYSQL_CREATE_BESTATS: bid=%s"%(bid))
    try:
        return DBPOOL['STATS'].runInteraction(mysql_query_create_bestats, bid)
    except Exception as e:
        dbg(1,"(%s) def MYSQL_CREATE_BESTATS: failed, exception = '%s'"%(e))
        return False

def mysql_query_create_bestats(txn, bid):
    try:
        query = "INSERT INTO `bestats` ( bid ) VALUES ('%d')" % (int(bid))
        result = txn.execute(query)
        if result:
            return result
    except Exception as e:
        if e[0] == 1062:
            # could not insert because db entry exists fine
            pass
        else:
            dbg(1,"def mysql_query_create_bestats: failed, exception = '%s'"%(e))
    return None

# MYSQL UPDATE BESTATS

def CALLBACK_UPDATE_BESTATS(result, bid, diffdata):
    try:
        if result != None:
            global BESTATS
            for key,diff in diffdata.viewitems():
                mkey = key + "_mysql"
                BESTATS[bid][mkey] += diff
                
                dbg(7,"def CALLBACK_UPDATE_BESTATS: bid = '%s', mkey = '%s', diff = '%s'"%(bid,mkey,diff))
            dbg(7,"def CALLBACK_UPDATE_BESTATS: bid='%s', result = '%s', diffdata = '%s'"%(bid,result,diffdata))
        return
    except Exception as e:
        dbg(1,"(%s) def CALLBACK_UPDATE_BESTATS: failed, exception = '%s'"%(e))


def MYSQL_UPDATE_BESTATS(bid,query):
    dbg(7,"def MYSQL_UPDATE_BESTATS: bid='%s', query = '%s'"%(bid,query))
    try:
        return DBPOOL['STATS'].runInteraction(mysql_query_update_bestats, query)
    except Exception as e:
        dbg(1,"(%s) def MYSQL_UPDATE_BESTATS: failed, exception = '%s'"%(e))
        return False

def mysql_query_update_bestats(txn, query):
    try:
        result = txn.execute(query)
        if result:
            return result
    except Exception as e:
        dbg(1,"def mysql_query_update_bestats: failed, exception = '%s'"%(e))
    return None

def check_update_bestats():
    global BESTATS
    allqueries = dict()
    alldiffs = dict()
    for bid,value in BESTATS.viewitems():
        dbg(7,"def check_update_bestats: bid='%s' value='%s'"%(bid,value))
        queries = list()
        diffdata = dict()
        rxbytes = 0
        article = 0
        nofound = 0
        choosen = 0
        failure = 0
        
        if value['rxbytes_local'] > value['rxbytes_mysql']:
            diff = value['rxbytes_local'] - value['rxbytes_mysql']
            querypart = "rxbytes = rxbytes + %d" % (int(diff))
            queries.append(querypart)
            diffdata['rxbytes'] = diff
            
        if value['txbytes_local'] > value['txbytes_mysql']:
            diff = value['txbytes_local'] - value['txbytes_mysql']
            querypart = "txbytes = txbytes + %d" % (int(diff))
            queries.append(querypart)
            diffdata['txbytes'] = diff
        
        if value['article_local'] > value['article_mysql']:
            diff = value['article_local'] - value['article_mysql']
            querypart = "article = article + %d" % (int(diff))
            queries.append(querypart)
            diffdata['article'] = diff
        
        if value['nofound_local'] > value['nofound_mysql']:
            diff = value['nofound_local'] - value['nofound_mysql']
            querypart = "nofound = nofound + %d" % (int(diff))
            queries.append(querypart)
            diffdata['nofound'] = diff
        
        if value['choosen_local'] > value['choosen_mysql']:
            diff = value['choosen_local'] - value['choosen_mysql']
            querypart = "choosen = choosen + %d" % (int(diff))
            queries.append(querypart)
            diffdata['choosen'] = diff
        
        if value['failure_local'] > value['failure_mysql']:
            diff = value['failure_local'] - value['failure_mysql']
            querypart = "failure = failure + %d" % (int(diff))
            queries.append(querypart)
            diffdata['failure'] = diff
        
        query = None
        if len(queries) > 0:
            updated = int(NOW())
            query = 'UPDATE `nntp_stats`.`bestats` SET '
            final = ", updated = '%d' WHERE `bid` = '%d' LIMIT 1" % (updated,bid)
            
            if len(queries) == 1:
                query = query + queries[0] + final
            else:
                l = 0
                for qp in queries:
                    if l == 0:
                        query += qp + ', '
                    elif l > 0 and l < len(queries)-1:
                        query = query + qp + ', '
                    else:
                        query = query + qp + final
                    l += 1
        
        if query != None:
            allqueries[bid] = query
            alldiffs[bid] = diffdata
            dbg(7,"def check_update_bestats: query = '%s'"%(query))
    
    if len(allqueries) > 0:
        for bid,query in allqueries.viewitems():
            diffdata = alldiffs[bid]
            MYSQL_UPDATE_BESTATS(bid,query).addCallback(CALLBACK_UPDATE_BESTATS, bid, diffdata).addErrback(SQLCONNFAIL)

# MYSQL LOAD BACKS

def mysql_query_load_backs(txn):
    try:
        txn.execute("SELECT bid,bgrp,name,host,port,user,passwd,conns,tout,retention,priority FROM backends WHERE enable = 1 AND priority >= 0 AND expire > %d ORDER BY priority,bgrp"%(int(NOW())))
        result = txn.fetchall()
        if result:
            return result
        else:
            return None
    except Exception as e:
        dbg(1,"def mysql_query_load_backs: failed, exception = '%s'"%e)
        return False

def MYSQL_LOAD_BACKS():
    try:
        return DBPOOL['BACKS'].runInteraction(mysql_query_load_backs)
    except Exception as e:
        dbg(1,"def MYSQL_LOAD_BACKS: failed, exception = '%s'"%e)
        return False

def CB_LOAD_BACKS(result):
    try:
        
        if result == False:
            return
        if result == None:
            result = dict()
        result_len = len(result)
        if result_len > 0:
            BACKENDS = list()
            dbg(7,"def CB_LOAD_BACKS: len = %s"%(result_len))
            global CONFIG
            global CURRENT_BACKEND_CONNS
            global BESTATS
            for entry in result:
                BACKEND = dict()
                BACKEND["BID"] = entry[0]
                BACKEND["GROUP"] = entry[1]
                BACKEND["NAME"] = entry[2]
                BACKEND["host"] = entry[3]
                BACKEND["port"] = entry[4]
                BACKEND["user"] = entry[5]
                BACKEND["pass"] = entry[6]
                BACKEND["conn"] = entry[7]
                BACKEND["tout"] = entry[8]
                BACKENDS.append(BACKEND)
                bid = len(BACKENDS)-1
                try:
                    conns = CURRENT_BACKEND_CONNS[bid]
                except:
                    CURRENT_BACKEND_CONNS.append(0)
                try:
                    bid = BACKEND["BID"]
                    if bid not in BESTATS:
                        BESTATS[bid] = dict()
                        
                        BESTATS[bid]['rxbytes_local'] = 0
                        BESTATS[bid]['rxbytes_mysql'] = 0
                        
                        BESTATS[bid]['txbytes_local'] = 0
                        BESTATS[bid]['txbytes_mysql'] = 0
                        
                        BESTATS[bid]['article_local'] = 0
                        BESTATS[bid]['article_mysql'] = 0
                        
                        BESTATS[bid]['nofound_local'] = 0
                        BESTATS[bid]['nofound_mysql'] = 0
                        
                        BESTATS[bid]['choosen_local'] = 0
                        BESTATS[bid]['choosen_mysql'] = 0
                        
                        BESTATS[bid]['failure_local'] = 0
                        BESTATS[bid]['failure_mysql'] = 0
                        
                        dbg(7,"def CB_LOAD_BACKS: BESTATS BID='%s'"%bid)
                        MYSQL_CREATE_BESTATS(bid).addCallback(CALLBACK_CREATE_BESTATS, bid).addErrback(SQLCONNFAIL)
                        
                except Exception as e:
                    dbg(1,"def CB_LOAD_BACKS: failed BESTATS, exception = '%s'"%e)
            if len(CONFIG["BACKENDS"]) != len(BACKENDS):
                CONFIG["BACKENDS"] = BACKENDS
                dbg(5,"def CB_LOAD_BACKS: UPDATED %d"%(result_len))
    except Exception as e:
        dbg(1,"def CB_LOAD_BACKS: failed #0, exception = '%s'"%e)

# MYSQL LOAD USERS

def mysql_query_load_users(txn):
    try:
        txn.execute("SELECT name,maxconns,passwd FROM users WHERE stoptime > %d"%NOW())
        result = txn.fetchall()
        if result:
            #print result
            return result
        else:
            return None
    except Exception as e:
        dbg(1,"def mysql_query_load_users: failed, exception = '%s'"%e)
        return False

def MYSQL_LOAD_USERS():
    try:
        return DBPOOL['USERS'].runInteraction(mysql_query_load_users)
    except Exception as e:
        dbg(1,"def MYSQL_LOAD_USERS: failed, exception = '%s'"%e)
        return False

def CB_LOAD_USERS(result):
    try:
        if result == False:
            return
        if result == None:
            result = dict()
        NEWUSERS = list()
        result_len = len(result)
        if result_len > 0:
            dbg(5,"def CB_LOAD_USERS: len = %s"%(result_len))
            global CONFIG
            
            # add users to dict
            for entry in result:
                name = entry[0]
                NEWUSERS.append(name)
                maxconns = int(entry[1])
                passwd = entry[2]
                
                if not name in CONFIG["USERS"]:
                    dbg(1,"def CB_LOAD_USERS: '%s' not in CONFIG[USERS]" % name)
                    CONFIG["USERS"][name] = passwd
                else:
                    if not CONFIG["USERS"][name] == passwd:
                        dbg(1,"def CB_LOAD_USERS: '%s' update passwd" % name)
                        CONFIG["USERS"][name] = passwd
                    
                if not name in CONFIG["CONNS"]:
                    CONFIG["CONNS"][name] = maxconns
                else:
                    if not CONFIG["CONNS"][name] == maxconns:
                        dbg(1,"def CB_LOAD_USERS: '%s' update maxconns = %d" % (name,maxconns))
                        CONFIG["CONNS"][name] = maxconns
            
        # remove not listed users
        if not CONFIG == False:
            if "USERS" in CONFIG and len(CONFIG["USERS"]) > 0:
                DELUSER = list()
                for name in CONFIG["USERS"]:
                    if not name in NEWUSERS:
                        DELUSER.append(name)
                
                for name in DELUSER:
                    dbg(1,"def CB_LOAD_USERS: remove '%s'"%(name))
                    del CONFIG["USERS"][name]
                    del CONFIG["CONNS"][name]
        
    except Exception as e:
        dbg(1,"def CB_LOAD_USERS: failed, exception = '%s'"%e)

# MYSQL USER CONNS TRAFFC

def mysql_query_update_user_traffic(txn, name, duration, rxbytes, notfound, articles, jumps):
    try:
        if name == None:
            return False
        if not name.isalnum():
            return False
        return txn.execute("UPDATE `users` SET `duration` = `duration` + %d, `rxbytes` = `rxbytes` + %d, `notfound` = `notfound` + %d, `articles` = `articles` + %d, `jumps` = `jumps` + %d WHERE `name` = '%s' LIMIT 1"%(int(duration),int(rxbytes),int(notfound),int(articles),int(jumps),name))
    except Exception as e:
        dbg(1,"def mysql_query_update_user_traffic: failed, exception = '%s'"%e)
        return False

def mysql_query_get_user_established_conns(txn, name):
    try:
        if not name.isalnum():
            return None
        txn.execute("SELECT established FROM users WHERE name = '%s' LIMIT 1"%(name))
        result = txn.fetchall()
        if result:
            #print result
            return result[0][0]
        else:
            return None
    except Exception as e:
        dbg(1,"def mysql_query_get_user_established_conns: failed, exception = '%s'"%e)
        return False

def mysql_query_update_user_established_conns(txn, name, direction):
    try:
        if name == None:
            return [0, name]
        if not name.isalnum():
            return [0, name]
        if direction == "up":
            result = txn.execute("UPDATE `users` SET `established` = `established` + 1 WHERE `name` = '%s' AND `established` < `maxconns` LIMIT 1"%(name))
        if direction == "down":
            result = txn.execute("UPDATE `users` SET `established` = `established` - 1 WHERE `name` = '%s' AND `established` > 0 LIMIT 1"%(name))
        if result:
            #print result
            return [result,name]
        else:
            return None
    except Exception as e:
        dbg(1,"def mysql_query_update_user_established_conns: failed, exception = '%s'"%e)
        return False

def MYSQL_GET_USER_ESTABLISHED_CONNS(name):
    dbg(7,"def MYSQL_GET_USER_ESTABLISHED_CONNS: '%s'"%name)
    try:
        return DBPOOL['USERS'].runInteraction(mysql_query_get_user_established_conns, name)
    except Exception as e:
        dbg(1,"def MYSQL_GET_USER_ESTABLISHED_CONNS: failed, exception = '%s'"%e)
        return False

# MYSQL FAIL

def SQLCONNFAIL(failure):
    dbg(1,"def SQLCONNFAIL(): failure = '%s'"%failure)
    return

# MYSQL END

def DEFERFAIL(failure):
    dbg(9,"def DEFERFAIL(): failure = '%s'"%failure)
    return

def CACHE_THREAD():
    dbg(0,"def CACHE_THREAD()")
    global CACHE_THREAD_RUNNING
    global META_CACHE
    global FLUSH_CACHE
    global LAST_ACTIONS
    global CURRENT_BACKEND_CONNS
    global LOGBCONNS
    global GLOBAL_SHUTDOWN
    global CLIENT_FACTS
    
    starttime = NOW()
    if CONFIG == False:
        return
    
    
    if os.path.isfile(SERVER_CLOSE_FILE):
        if GLOBAL_SHUTDOWN == False:
            dbg(1,"ADMIN: SERVER CLOSE %s"%(SERVER_CLOSE_FILE))
            GLOBAL_SHUTDOWN = True
    else:
        if GLOBAL_SHUTDOWN == True:
            dbg(1,"ADMIN: SERVER OPEN")
            GLOBAL_SHUTDOWN = False

    if LAST_ACTIONS["MYSQL_LOAD_USERS"] < int(NOW() - 300):
        dbg(7,"def CACHE_THREAD: MYSQL_LOAD_USERS")
        try:
            MYSQL_LOAD_USERS().addCallback(CB_LOAD_USERS).addErrback(SQLCONNFAIL)
        except Exception as e:
            dbg(1,"def CACHE_THREAD: MYSQL_LOAD_USERS() failed, exception = '%s'"%e)
        LAST_ACTIONS["MYSQL_LOAD_USERS"] = int(NOW())
    
    if LAST_ACTIONS["MYSQL_LOAD_BACKS"] < int(NOW() - 3600):
        dbg(5,"def CACHE_THREAD: MYSQL_LOAD_BACKS")
        try:
            MYSQL_LOAD_BACKS().addCallback(CB_LOAD_BACKS).addErrback(SQLCONNFAIL)
        except Exception as e:
            dbg(1,"def CACHE_THREAD: MYSQL_LOAD_BACKS() failed, exception = '%s'"%e)
        LAST_ACTIONS["MYSQL_LOAD_BACKS"] = int(NOW())
    
    if LAST_ACTIONS["MYSQL_UPDATE_BESTATS"] < int(NOW() - random.randint(10,30)) \
        and LAST_ACTIONS["MYSQL_LOAD_BACKS"] < int(NOW() - 3):
        dbg(7,"def CACHE_THREAD: MYSQL_UPDATE_BESTATS")
        check_update_bestats()
        LAST_ACTIONS["MYSQL_UPDATE_BESTATS"] = int(NOW())
    
    if LAST_ACTIONS["MYSQL_CLEAR_BACKEND_SESSIONS"] == 0 \
        and LAST_ACTIONS["MYSQL_UPDATE_BESTATS"] > 0 \
        and LAST_ACTIONS["MYSQL_UPDATE_BESTATS"] < int(NOW() - 3):
        dbg(5,"def CACHE_THREAD: MYSQL_CLEAR_BACKS")
        try:
            MYSQL_CLEAR_BACKEND_SESSIONS().addCallback(CALLBACK_CLEAR_BACKEND_SESSIONS).addErrback(SQLCONNFAIL)
        except Exception as e:
            dbg(1,"def CACHE_THREAD: MYSQL_CLEAR_BACKS() failed, exception = '%s'"%e)
        LAST_ACTIONS["MYSQL_CLEAR_BACKEND_SESSIONS"] = int(NOW())
        if GLOBAL_SHUTDOWN == True:
            GLOBAL_SHUTDOWN = False
            dbg(1,"def CACHE_THREAD: SERVER OPEN")
    
    try:
        CFG = CONFIG["CACHING"]
        
        if LAST_ACTIONS["RUN_CACHE_THREAD"] == 0: 
            dbg(1,"def CACHE_THREAD: booted")
            LAST_ACTIONS["RUN_CACHE_THREAD"] = int(NOW())
        
        if FLUSH_CACHE == True or LAST_ACTIONS["RUN_CACHE_THREAD"] < int(NOW()+CFG["flush_cache_every"]):
            dbg(9,'def CACHE_THREAD: TIME LIMIT')
        else:
            dbg(9,'def CACHE_THREAD: leaving')
            CACHE_THREAD_RUNNING = False
            return
        
        # check backend conns
        try:
            i = 0
            popit = {}
            for data in CONFIG["BACKENDS"]:
                #dbg(1,"ADMIN INFO: data = '%s'"%data)
                backend_id = i
                if backend_id in LOGBCONNS:
                    for connid,time in LOGBCONNS[backend_id].items():
                        if not connid in CONN_ID:
                            if not backend_id in popit:
                                popit[backend_id] = []
                            popit[backend_id].append(connid)
                            dbg(1,"def CACHE THREAD: removed '%s' backend '%s'"%(connid,backend_id))
                i += 1
            
            if len(popit) > 0:
                for bid,list in popit.items():
                    for connid in list:
                        if connid in LOGBCONNS[bid]:
                            LOGBCONNS[bid].pop(connid, None)
                            CURRENT_BACKEND_CONNS[bid] = max(0, CURRENT_BACKEND_CONNS[bid] - 1)
                            try: del CONN_ID[connid]["TCPC"][bid]
                            except: pass
                            try:
                                del CLIENT_FACTS[connid][bid]
                            except Exception as e:
                                dbg(1,"def CACHE_THREAD: del CLIENT_FACTS[%s][%s]  failed, exception = '%s'"%(connid,bid,e))
                            
                            try:
                                MYSQL_SET_BACKEND_SESSION(connid, GET_BACKEND_INFO(bid,'BID'), 'noname', "down", src = 'CACHE_THREAD').addCallback(CALLBACK_SET_BACKEND_SESSION, "up", connid).addErrback(SQLCONNFAIL)
                            except Exception as e:
                                dbg(1,"(%s) MYSQL_SET_BACKEND_SESSION failed #2, exception = '%s'"%(connid,e))
                            
                            dbg(7,"def CACHE_THREAD: connerror, removed connid '%s' from backend_id %s"%(list,bid))
            
        except Exception as e:
            dbg(1,"def CACHE_THREAD: error check backend conns failed, exception = '%s'"%e)
        
        poplist = []
        # clear not found objects from mem
        try:
            max_notf_cache = CFG["max_notf_cache"]
            max_notf_etime = CFG["max_notf_etime"]
            
            if len(META_CACHE["NOTF"]) > max_notf_cache \
                or LAST_ACTIONS["FORCE_NOTF_EXPIRE"] < int(NOW() - 3600):
                for msgid,etime in META_CACHE["NOTF"].items():
                    killtime = NOW()-max_notf_etime
                    if etime < killtime:
                        poplist.append(msgid)
                if LAST_ACTIONS["FORCE_NOTF_EXPIRE"] < int(NOW() - 3600):
                    LAST_ACTIONS["FORCE_NOTF_EXPIRE"] = int(NOW())
            if len(poplist) > 0:
                for msgid in poplist:
                    del META_CACHE["NOTF"][msgid]
                dbg(3,"def CACHE_THREAD: cleared %d notf objects. remaining notf in mem objects=%d"%(len(poplist), len(META_CACHE["NOTF"])))
        
        except Exception as e:
            dbg(1,"def CACHE_THREAD: JOB4 failed, exception = '%s'"%(e))
        
        dbg(9,"def CACHE_THREAD: leaving final")
    except Exception as e:
       dbg(1,"def CACHE_THREAD: failed, exception = '%s'"%(e))
    
    runtime = int((NOW()-starttime)*1000)
    dbg(9,"def CACHE_THREAD: runtime %s ms, len CONN_ID == %d"%(runtime, len(CONN_ID) ))
    LAST_ACTIONS["RUN_CACHE_THREAD"] = int(NOW())
    CACHE_THREAD_RUNNING = False
    FLUSH_CACHE = False
    return

def IDLE_TIMER():
    global CACHE_THREAD_RUNNING
    global IDLE_TIMER_RUNNING
    global CONN_ID
    global LAST_ACTIONS
    global DELETE_THREAD_RUNNING
    
    if LAST_ACTIONS["CFG_RELOAD"] < (int(NOW())-RELOAD_CONFIG_EVERY):
        CONFIG_LOAD()
    
    if CACHE_THREAD_RUNNING == False:
        dbg(9,"join CACHE_THREAD")
        CACHE_THREAD_RUNNING = True
        thread = threading.Thread(name='CACHE_THREAD',target=CACHE_THREAD)
        thread.daemon = True
        thread.start()
    
    #if LAST_ACTIONS["GC_COLLECT"] < int(NOW()-random.randint(30,90)):
    #    dbg(9,"def IDLE_TIMER: run garbage collection")
    #    thread = threading.Thread(name='gc.collect',target=gc.collect)
    #    thread.daemon = True
    #    thread.start()
    #    LAST_ACTIONS["GC_COLLECT"] = int(NOW())
    
    time.sleep(0.5)
    thread = threading.Thread(name='IDLE_TIMER',target=IDLE_TIMER)
    thread.daemon = True
    thread.start()

def TEMP_BLOCK_USER(name):
    dbg(1,"def TEMP_BLOCK_USER: %s"%name)
    global TEMPORARY_BLOCKED
    TEMPORARY_BLOCKED[name] = int(NOW())

def DEL_TEMP_BLOCK_USER(name):
    global TEMPORARY_BLOCKED
    if name in TEMPORARY_BLOCKED:
        del TEMPORARY_BLOCKED[name]

class Frontend(NNTPServer):
    auth_user = None
    clientFactory = None
    client = None
    transport = None
    
    def makeConnection(self, transport):
        global CURRENT_TEMP_CONNS
        global CONN_ID
        self.remove_conn_on_lost = True
        self.transport = transport
        self.conn_start = int(NOW())
        
        self.conn_id = "%sx%sx%s" % (self.conn_start,random.randint(1,(2**16)),CONFIG['LPORT'])
        if self.conn_id in CONN_ID:
            self.sendLine('502 RETRY')
            self.transport.loseConnection()
            
        self.pre_auth_user = None
        self.auth_user = None
        self.bgrp = None
        self.msgid = None
        self.msgid_state = 0
        self.line = None
        self.cmd = None
        self.accept_request = False
        self.rtt = 0
        self.wait_response_rounds = 0
        
        self.switch_backend = False
        self.turns = 0
        self.notfound_count = 0
        self.backend_jumps = 0
        self.BEid = -1
        self.wait_sum = 0
        self.response_calltimes = dict()
        self.gotBytes = 0
        self.reader = False
        
        CONN_ID[self.conn_id] = {}
        CONN_ID[self.conn_id]["TIME"] = self.conn_start # frontend session start time
        CONN_ID[self.conn_id]["LAST"] = int(NOW())               # frontend last request
        CONN_ID[self.conn_id]["LCMD"] = None            # frontend last command
        CONN_ID[self.conn_id]["TCPC"] = dict()            # frontend connfrontend conn has no reactor
        CONN_ID[self.conn_id]["USER"] = None            # frontend conn has no user
        CONN_ID[self.conn_id]["BACK"] = False           # frontend conn has no backend
        CONN_ID[self.conn_id]["ARTS"] = 0               # counter found articles
        CONN_ID[self.conn_id]["NOTF"] = 0               # counter not found articles
        CONN_ID[self.conn_id]["RX_BYTES"] = 0           # zero!
        CONN_ID[self.conn_id]["JUMP"] = -1              # initial jump = -1
        CONN_ID[self.conn_id]["INIT"] = -1              # initial backend id
        
        CURRENT_TEMP_CONNS += 1
        self.connectionMade(connect=False)
    
    def check_authed(self):
        """ fixme: needs to be called with deferred """
        try:
            if self.auth_user == None:
                self.transport.loseConnection()
                dbg(5,"(%s) def check_authed: loseConnection() (%s/%s)"%(self.conn_id,self.auth_user,self.pre_auth_user))
            else:
                pass
                reactor.callLater(15, self.check_idle)
        except Exception as e:
            dbg(5,"(%s) def check_authed: failed, exception = '%s'"%(self.conn_id,e))
    
    def check_idle(self):
        """ fixme: needs to be called with deferred """
        try:
            if CONN_ID[self.conn_id]["LAST"] <= int(NOW()-MAX_IDLE_TIME):
                self.sendLine('205 IDLE')
                self.transport.loseConnection()
                dbg(5,"(%s) def check_idle: loseConnection() (%s)"%(self.conn_id,self.auth_user))
            else:
                reactor.callLater(30, self.check_idle)
        except Exception as e:
            dbg(5,"(%s) def check_idle: failed, exception = '%s'"%(self.conn_id,e))
    
    def connectionMade(self,connect=False,src=None):
        global CONN_ID
        global CLIENT_FACTS
        
        if connect == False:
            self.sendLine(MSG_WELCOME)
            #reactor.callLater(MAX_NOAUTH_TIME, self.check_authed)
        else:
            dbg(7,"(%s) Frontend def connectionMade: connect = %s, src = '%s', BEid '%s', self.client = '%s'"%(self.conn_id,connect,src,self.BEid,self.client))
            
            if self.BEid == -1:
                self.BEid = self.get_free_backend_id(src='Frontend def connectionMade')
            else:
                dbg(7,"(%s) Frontend def connectionMade: #0.0 backend %s %s"%(self.conn_id,self.BEid,CONFIG["BACKENDS"][self.BEid]["NAME"]))
                
                try:
                    if self.conn_id not in CLIENT_FACTS:
                        dbg(7,"(%s) Frontend def connectionMade: create CLIENT_FACTS[self.conn_id]"%(self.conn_id))
                        CLIENT_FACTS[self.conn_id] = dict()
                    
                    if self.BEid not in CLIENT_FACTS[self.conn_id]:
                        CLIENT_FACTS[self.conn_id][self.BEid] = dict()
                        CLIENT_FACTS[self.conn_id][self.BEid]['prot'] = None
                    
                except Exception as e:
                    dbg(1,"(%s) Frontend def connectionMade: failed #1, exception = '%s'"%(self.conn_id,e))
                
                try:
                    dbg(7,"(%s) Frontend def connectionMade: #0.2"%(self.conn_id))
                    
                    if CLIENT_FACTS[self.conn_id][self.BEid]['prot'] != None:
                        
                        self.client = CLIENT_FACTS[self.conn_id][self.BEid]['prot']
                        
                        self.switch_backend = False
                        CONN_ID[self.conn_id]["BACK"] = True
                        
                        dbg(7,"(%s) Frontend def connectionMade: RE-USE backend %s %s, switch_backend = %s, clientFactory = '%s'"%(self.conn_id,self.BEid,CONFIG["BACKENDS"][self.BEid]["NAME"],self.switch_backend,self.clientFactory))
                        
                    else:
                        
                        client = self.clientFactory()
                        client.server = self
                        connector = reactor.connectTCP(CONFIG["BACKENDS"][self.BEid]["host"], CONFIG["BACKENDS"][self.BEid]["port"], client, timeout=CONFIG["BACKENDS"][self.BEid]["tout"])
                        CONN_ID[self.conn_id]["TCPC"][self.BEid] = connector
                        dbg(7,"(%s) Frontend def connectionMade: #1 connect backend %s %s"%(self.conn_id,self.BEid,CONFIG["BACKENDS"][self.BEid]["NAME"]))
                        
                except Exception as e:
                    dbg(1,"(%s) Frontend def connectionMade: failed #2, exception = '%s'"%(self.conn_id,e))
    
    def connectionLost(self, reason):
        reason = clean_REASON(reason)
        
        dbg(7,"Frontend connectionLost: (%s) %s"%(self.conn_id,reason))
        global CURRENT_BACKEND_CONNS
        global FRONTEND_USER_CONNS
        global CURRENT_TEMP_CONNS
        global CONN_ID
        global META_CACHE
        
        if self.auth_user != None and self.auth_user in FRONTEND_USER_CONNS:
            FRONTEND_USER_CONNS[self.auth_user] = max(0, FRONTEND_USER_CONNS[self.auth_user] - 1)
            self.MYSQL_UPDATE_USER_ESTABLISHED_CONNS(self.auth_user,self.conn_id,"down").addCallback(self.CALLBACK_UPDATE_USER_ESTABLISHED_CONNS, "down").addErrback(SQLCONNFAIL)
        elif self.pre_auth_user != None and self.pre_auth_user in FRONTEND_USER_CONNS:
            FRONTEND_USER_CONNS[self.pre_auth_user] = max(0, FRONTEND_USER_CONNS[self.pre_auth_user] - 1)
        
        if self.remove_conn_on_lost == True:
            CURRENT_TEMP_CONNS = max(0, CURRENT_TEMP_CONNS - 1)
        
        duration_total = int(NOW() - self.conn_start)
        duration_wait = int(duration_total - self.wait_sum)
        
        rx_bytes = CONN_ID[self.conn_id]["RX_BYTES"]
        notfound = CONN_ID[self.conn_id]["NOTF"]
        articles = CONN_ID[self.conn_id]["ARTS"]
        jumps = max(0, CONN_ID[self.conn_id]["JUMP"])
        if rx_bytes > 0 and duration_wait > 0 and duration_total > 0:
            speed_total = int(rx_bytes / duration_total / 1024)
            speed_wait = int(rx_bytes / duration_wait / 1024)
        else:
            speed = 0
            speed_total = 0
            speed_wait = 0
        
        if self.auth_user is not None:
            if rx_bytes > 0 or notfound > 0 or articles > 0 or jumps > 0:
                self.MYSQL_UPDATE_USER_TRAFFIC(self.auth_user,duration_total,rx_bytes,notfound,articles,jumps).addCallback(self.CALLBACK_UPDATE_USER_TRAFFIC, self.auth_user).addErrback(SQLCONNFAIL)
            message  = "(%s) F_DIS: '%s' dur=%d/%d rxbytes=%d kbs=%d/%d arts=%d/notf=%d j=%d"  % (self.conn_id, self.auth_user, duration_total, duration_wait, rx_bytes, speed_total, speed_wait, articles, notfound, jumps)
            dbg(1,message)
        
        if self.client is not None:
            dbg(5,"(%s) Frontend: def connectionLost() Closing Backend %s %s"%(self.conn_id,self.BEid,GET_BACKEND_INFO(self.BEid,'NAME')))
            try:
                self.client.transport.loseConnection()
            except Exception as e:
                dbg(5,"(%s) Frontend: def connectionLost failed, exception = '%s'"%(self.conn_id,e))
            
            self.client = None
        
        del CONN_ID[self.conn_id]
        return
    
    def lineReceived(self, line):
        if self.reader == True:
            dbg(5,"(%s) F) def lineReceived: '%s'"%(self.conn_id,line))
        global CONFIG
        global CURRENT_BACKEND_CONNS
        global FRONTEND_USER_CONNS
        global CURRENT_TEMP_CONNS
        global GLOBAL_SHUTDOWN
        global FLUSH_CACHE
        global LAST_ACTIONS
        global META_CACHE
        
        lined = False
        LINEU = line.upper()
        
        if self.accept_request == False:
            return
        else:
            self.accept_request = False
        
        if LINEU == 'MODE READER':
            #self.sendLine('500 ERROR')
            #return self.transport.loseConnection()
            self.sendLine('480 SEND USER')
            self.reader = True
        
        elif LINEU.startswith('AUTHINFO USER '):
            if GLOBAL_SHUTDOWN == True:
                self.sendLine('502 SYSTEM UPDATE')
                return self.transport.loseConnection()
            
            data = line.split(' ')
            if len(data) == 3 and data[2].isalnum():
                self.pre_auth_user = data[2].strip()
                self.sendLine('381 SEND PASS')
            else:
                lined = '481 INVALID FORMAT'
        elif LINEU.startswith('AUTHINFO PASS '):
            data = line.split(' ')
            
            if self.pre_auth_user in TEMPORARY_BLOCKED:
                blocksec = 900
                if TEMPORARY_BLOCKED[self.pre_auth_user] > int(NOW() - blocksec):
                    diff = int(NOW() - TEMPORARY_BLOCKED[self.pre_auth_user])
                    wait = blocksec-diff
                    self.sendLine('481 502 RETRY in %s sec'%wait)
                    dbg(5,"(%s) error 500 CB '%s'"%(self.conn_id,self.pre_auth_user))
                    self.transport.loseConnection()
                    return
                else:
                    DEL_TEMP_BLOCK_USER(self.pre_auth_user)
            
            if len(data) == 3 and self.pre_auth_user in CONFIG["USERS"] and CONFIG["USERS"].get(self.pre_auth_user) == hashlib.sha256(data[2].strip()).hexdigest():
                
                # fixme 1
                if self.pre_auth_user not in FRONTEND_USER_CONNS:
                    FRONTEND_USER_CONNS[self.pre_auth_user] = 1
                else: 
                    FRONTEND_USER_CONNS[self.pre_auth_user] += 1
                CURRENT_TEMP_CONNS = max(0, CURRENT_TEMP_CONNS - 1)
                
                if self.pre_auth_user in CONFIG["CONNS"]:
                    # fixme 2
                    if FRONTEND_USER_CONNS[self.pre_auth_user] <= CONFIG["CONNS"][self.pre_auth_user]:
                        # Frontend connection authenticated, check mysql for established conns
                        MYSQL_GET_USER_ESTABLISHED_CONNS(self.pre_auth_user).addCallback(self.cb_user_established_conns).addErrback(SQLCONNFAIL)
                        # joined defered to sql query real maxconns
                    else:
                        lined = '502 LIMIT LOCAL'
                        TEMP_BLOCK_USER(self.pre_auth_user)
                else:
                    lined = '481 CONFIG FAIL'
            else:
                lined = '481 INVALID LOGIN'
        elif LINEU == 'HELP':
            lined = '505 SOS'
        elif LINEU == 'QUIT':
            lined = '205 CYA'
        elif LINEU.startswith('ADMIN AUTH '):
            if ADMINPWD == None:
                self.sendLine('999 ADMINPWD NOT SET')
                dbg(1,"(%s) ADMIN: ADMINPWD NOT SET"%(self.conn_id))
            else:
                getPWD = line.split(' ')[2]
                if getPWD == ADMINPWD:
                    getACTION = line.split(' ')[3].upper()
                    allow_actions = ('INFO','DEBUG','CLOSE','OPEN','PRINT')
                    if getACTION in allow_actions:
                        
                        if getACTION == 'INFO':
                            dbg(1,"(%s) ADMIN INFO"%(self.conn_id))
                            
                            fconns = 0
                            for user,conns in FRONTEND_USER_CONNS.items():
                                fconns += conns
                            
                            lines = [
                                    'SENT: %d'%(META_CACHE["SENT"]),
                                    'NOTF: %d/%d'% (len(META_CACHE["NOTF"]),CONFIG["CACHING"]["max_notf_cache"]),
                                    '________________'
                                    ]
                            
                            # check backend conns
                            i = 0
                            bconns = 0
                            tconns = 0
                            popit = {}
                            for data in CONFIG["BACKENDS"]:
                                dbg(1,"ADMIN INFO: data = '%s'"%data)
                                backend_id = i
                                bconns += CURRENT_BACKEND_CONNS[backend_id]
                                tconns += CONFIG["BACKENDS"][backend_id]["conn"]
                                xc = 0
                                xconns = 0
                                if backend_id in LOGBCONNS:
                                    xconns = len(LOGBCONNS[backend_id])
                                    for connid,time in LOGBCONNS[backend_id].viewitems():
                                        if not connid in CONN_ID:
                                            xc +=1
                                            dbg(1,"ADMIN INFO: connid '%s' @ backend %d not in CONN_ID"%(connid,backend_id))
                                line = 'Backend: %d %s %s @ %d/%d [%d/%d] Conns' % (backend_id,CONFIG["BACKENDS"][backend_id]["NAME"],CONFIG["BACKENDS"][backend_id]["GROUP"],CURRENT_BACKEND_CONNS[backend_id],CONFIG["BACKENDS"][backend_id]["conn"],xconns,xc)
                                lines.append(line)
                                i += 1
                            
                            lines.append('Frontend: %d Conns (Temp %d)' % (fconns,CURRENT_TEMP_CONNS))
                            lines.append('Backends: %d/%d Conns' % (bconns,tconns))
                            
                            # list connected clients on frontend
                            for conn_id in CONN_ID:
                                if not self.conn_id == conn_id:
                                    lines.append("CONN_ID = %s: '%s'"%(conn_id,CONN_ID[conn_id]))
                                
                            # print all lines
                            for line in lines:
                                self.sendLine(line)
                                dbg(1,"ADMIN INFO: %s"%line)
                            
                        if getACTION == 'DEBUG':
                            global DEBUG_LEVEL
                            try:
                                lvl = int(line.split(' ')[4])
                                if lvl <= 0 or lvl > 9:
                                    lvl = 1
                            except:
                                lvl = 1
                            DEBUG_LEVEL = lvl
                            dbg(1,"(%s) ADMIN: NEW DEBUG_LEVEL %d"%(self.conn_id,DEBUG_LEVEL))
                            self.sendLine('NEW DEBUG_LEVEL %d'%(DEBUG_LEVEL))
                            self.transport.loseConnection()
                            
                        elif getACTION == 'FLUSH':
                            self.sendLine('FLUSHING CACHE')
                            FLUSH_CACHE = True
                            LAST_ACTIONS["RUN_CACHE_THREAD"] = 1
                            dbg(1,"(%s) ADMIN: FLUSH CACHE"%(self.conn_id))
                        elif getACTION == 'CLOSE':
                            GLOBAL_SHUTDOWN = True
                            FLUSH_CACHE = True
                            self.sendLine('SERVER CLOSED')
                            dbg(1,"(%s) ADMIN: SERVER CLOSE"%(self.conn_id))
                            self.transport.loseConnection()
                        
                        elif getACTION == 'OPEN':
                            GLOBAL_SHUTDOWN = False
                            self.sendLine('SERVER OPEN')
                            dbg(1,"(%s) ADMIN: SERVER OPEN"%(self.conn_id))
                            self.transport.loseConnection()
                            
                        elif getACTION == 'PRINT':
                            self.sendLine('PRINT DEBUG')
                            dbg(1,"ADMIN LOGBCONNS: %s"%(LOGBCONNS))
                            
                            for key in META_CACHE:
                                dbg(1,"ADMIN PRINT: key %s '%s'"%(key,META_CACHE[key]))
                    else:
                        self.sendLine('INVALID ACTION')
                        dbg(1,"(%s) ADMIN: INVALID ACTION"%(self.conn_id))
                else:
                    self.sendLine('999 ADMIN PWD FAIL')
                    dbg(1,"(%s) ADMIN: PWD FAIL"%(self.conn_id))
        
        elif LINEU == 'CAPABILITIES':
            self.sendLine('101 Capabilities list:')
            self.sendLine('VERSION 1')
            self.sendLine('AUTHINFO USER PASS')
            self.sendLine('.')
            return
        
        else:
            lined = '500 NOP' # 480 no permission
        
        # overwrite line to frontend
        if not lined == False:
            dbg(5,"(%s) def LineReceived: error lined '%s/%s' '%s/%s'" % (self.conn_id, self.pre_auth_user, self.auth_user, lined,line))
            self.sendLine(lined)
            self.transport.loseConnection()

    def sendLine(self, line, log=True):
        """
        if self.reader == True and log == True:
            try:
                line.decode('ascii')
                try:
                    dbg(5,"(%s) F) def sendLine: '%s'"%(self.conn_id,line))
                except:
                    dbg(6,"(%s) F) def sendLine: failed, exception = '%s'"%(self.conn_id,e))
            except UnicodeDecodeError:
                pass
        
        2017-07-08 07:17:18+0200 [-] 1499491038.94:  (1499490919x41719x11911) F) def _LTB 'ARTICLE <oJGR0ykBhrfagkniJERD@JBinUp.local>'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: '220 0 <oJGR0ykBhrfagkniJERD@JBinUp.local>'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Path: not-for-mail'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'From: ghost <upload@ghost-of-usenet.org>'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Newsgroups: alt.binaries.ath,alt.binaries.bloaf,alt.binaries.ghosts,alt.binaries.hdtv,alt.binaries.hdtv.german,alt.binaries.mom'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Subject: >>ghost-of-usenet.org>>Stirb.Langsam.II.1990.German.DTS.1080p.BluRay.x264-DETAiLS<<Sponsored by Astinews<< [49/63]-["stirbl2.part41.rar"] yEnc (406/521)'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'X-No-Archive: yes'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Message-ID: <oJGR0ykBhrfagkniJERD@JBinUp.local>'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'X-Newsreader: JBinUp 0.90 Beta 7 - Build: 2008120403 (http://www.JBinUp.com)'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Date: 26 Jun 2009 20:19:28 GMT'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Lines: 3062'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: 'Organization: AstiNews'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: ''
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: '=ybegin part=406 total=521 line=128 size=200000000 name=stirbl2.part41.rar'
        2017-07-08 07:17:18+0200 [-] 1499491038.99:  (1499490919x41719x11911) F) def sendLine: '=ypart begin=155520001 end=155904000'
        2017-07-08 07:17:18+0200 [-] print failed, exception = ''ascii' codec can't decode byte 0x82 in position 62: ordinal not in range(128)'
        2017-07-08 07:17:18+0200 [-] print failed, exception = ''ascii' codec can't decode byte 0xff in position 62: ordinal not in range(128)'
        2017-07-08 07:17:18+0200 [-] print failed, exception = ''ascii' codec can't decode byte 0xc3 in position 60: ordinal not in range(128)'
        """
        self.transport.write(line + self.delimiter)
        self.accept_request = True
    
    def _LineToBackend(self, line):
        if self.reader == True:
            dbg(5,"(%s) F) def _LTB '%s'"%(self.conn_id,line))
        try:
            # called everytime a frontend connection passed a line after authentication
            
            global CONN_ID
            global META_CACHE
            
            if GLOBAL_SHUTDOWN == True:
                self.sendLine('500 SYSTEM UPDATE: retry in few minutes!')
                self.transport.loseConnection()
                return
            
            if self.accept_request == False:
                self.sendLine('400 DOH')
                self.transport.loseConnection()
                dbg(1,"(%s) def _LineToBackend: self.server.accept_request == False, line = '%s'"%(self.conn_id,line))
                return
            else:
                self.accept_request = False
            
            if self.waited <= 0:
                self.waited = NOW()
            
            #if not self.conn_id in CONN_ID:
            #    return
            
            lined = False
            liner = False
            pass_request = False
            send_request = False
            
            CONN_ID[self.conn_id]["LAST"] = int(NOW())
            
            LINEU = line.upper()
            
            if self.conn_start < int(NOW()-SESSION_TIMEOUT):
                lined = '500 RE-CONN'
            
            elif self.msgid == None:
                if LINEU.startswith(ALLOW_CMDS):
                    SPLITLINE = line.split(' ')
                    if len(SPLITLINE) == 2:
                        newmsgid = SPLITLINE[1]
                        cmd = SPLITLINE[0].upper()
                        
                        if cmd in ALLOW_CMDS \
                            and newmsgid.startswith('<') \
                            and newmsgid.endswith('>'):
                            
                            self.msgid = newmsgid
                            self.cmd = cmd
                            #self.line = line
                            CONN_ID[self.conn_id]["LCMD"] = self.cmd
                        else:
                            lined = '500 CMD'
                    else:
                        lined = '500 MFM'
            
            if lined == False:
                if not LINEU.startswith(ALLOW_CMDS):
                    if LINEU.startswith('QUIT'):
                        # user sent QUIT, we follow!
                        lined = '205 CYA'
                        
                    elif LINEU.startswith('XFEAT'):
                        liner = '502 NO XFEAT'
                        
                    elif LINEU.startswith('AUTHINFO USER'):
                        return self.sendLine('381 SEND PASS?')
                        
                    elif LINEU.startswith('AUTHINFO PASS'):
                        return self.sendLine('281 OK (no posting)')
                        
                    elif LINEU  == 'LIST OVERVIEW.FMT':
                        self.cmd = 'LIST_OVERVIEW'
                        
                    else:
                        if self.reader == False:
                            lined = '502 NO CMD'
            
            if self.reader == True:
                if LINEU.startswith('MODE'):
                    if LINEU == 'MODE READER':
                        return self.sendLine(MSG_WELCOME + ' (reader)')
                    else:
                        lined = '502 NO MODE'
                        self.reader = False
                
                elif LINEU == 'LIST':
                    self.cmd = 'LIST'
                
                elif LINEU  == 'LIST OVERVIEW.FMT':
                    self.cmd = 'LIST_OVERVIEW'
            
            if lined != False:
                # overwrite response to frontend
                
                dbg(1,"def _LineToBackend: (%s) error lined='%s' line='%s' (%s/%s)"%(self.conn_id,lined,line,self.auth_user,self.pre_auth_user))
                self.sendLine(lined)
                return self.transport.loseConnection()
            
            elif liner != False:
                # overwrite response to frontend without closing
                dbg(1,"def _LineToBackend: (%s) error liner='%s' line='%s' (%s/%s)"%(self.conn_id,liner,line,self.auth_user,self.pre_auth_user))
                return self.sendLine(liner)
            
            if self.msgid == None and self.reader == False:
                dbg(1,"def _LineToBackend: (%s) error line='%s', msgid==None (%s/%s)"%(self.conn_id,line,self.auth_user,self.pre_auth_user))
                return
            
            if self.msgid != 0:
                if self.msgid in META_CACHE["NOTF"]:
                    self.sendLine('430 NOA (%s)'%(CONFIG["LPORT"]))
                    
                    if DEBUG_LEVEL >= 5: dbg(5,"(%s) CACHE: HIT R:430 '%s' (%s)"%(self.conn_id,self.msgid,self.auth_user))
                    else: dbg(3,"(%s) CACHE: HIT R:430 (%s)"%(self.conn_id,self.auth_user))
                    
                    self.msgid = None
                    self.msgid_state = 0
                    self.accept_request = True
                    return
            
            if self.msgid != None or self.reader == True:
                if self.cmd != None:
                    self.line = line
                    send_request = True
                    pass_request = True
            
            if pass_request == True:
                
                if self.msgid.startswith('<IS.NOT@HERE.TEST'):
                    seld.sendLine('430 TST (%s)'%(CONFIG['LPORT']))
                    return
                    
                if self.client == None:
                    self.waited = NOW()
                    
                    if CONN_ID[self.conn_id]["JUMP"] == -1 or self.BEid == -1:
                        CONN_ID[self.conn_id]["JUMP"] = 0
                        self.connectionMade(connect=True)
                    
                    dbg(7,"def _LineToBackend: (%s) self.client == None, join deferred  self.f_cb_wait_backend()"%(self.conn_id))
                    self.deferred_f_wait_backend = self.f_wait_backend()
                    self.deferred_f_wait_backend.addCallback(self.f_cb_wait_backend).addErrback(DEFERFAIL)
                
                elif CONN_ID[self.conn_id]["BACK"] == False:
                    self.waited = NOW()
                    
                    dbg(7,"(%s) def _LineToBackend: send auth to backend %s"%(self.conn_id,GET_BACKEND_INFO(self.BEid,'NAME')))
                    self.deferred_f_auth_backend = self.f_auth_backend()
                    self.deferred_f_auth_backend.addCallback(self.f_cb_auth_backend).addErrback(DEFERFAIL)
                    self.client.authInfo()
                    self.switch_backend = True
                
                elif pass_request == True and send_request == True:
                    self.waited = NOW()
                    
                    if self.msgid_state == 8430:
                        self.msgid_state = 8431
                    
                    if self.cmd == 'BODY':
                        dbg(7,"def _LineToBackend: (%s) fetchBody '%s'"%(self.conn_id,self.msgid))
                        self.client.fetchBody(self.msgid)
                    
                    elif self.cmd == 'ARTICLE':
                        dbg(7,"def _LineToBackend: (%s) fetchArticle '%s'"%(self.conn_id,self.msgid))
                        self.client.fetchArticle(self.msgid)
                    
                    elif self.cmd == 'HEAD':
                        dbg(7,"def _LineToBackend: (%s) fetchHead '%s'"%(self.conn_id,self.msgid))
                        self.client.fetchHead(self.msgid)
                    
                    elif self.cmd == 'LIST_OVERVIEW':
                        dbg(5,"def _LineToBackend: (%s) fetchOverview '%s'"%(self.conn_id,self.cmd))
                        self.client.fetchOverview()
                    
                    
                    elif self.cmd == 'LIST':
                        dbg(5,"def _LineToBackend: (%s) fetchGroups '%s'"%(self.conn_id,self.cmd))
                        if not len(NEWSGROUPS_LIST):
                            self.client.fetchGroups()
                        else:
                            self.client.gotAllGroups(NEWSGROUPS_LIST)
                    
                    
                    self.deferred_wait_response = self.f_wait_response()
                    self.deferred_wait_response.addCallback(self.f_cb_wait_response).addErrback(DEFERFAIL)
        except Exception as e:
            dbg(1,"(%s) def _LineToBackend: failed, exception = '%s'"%(self.conn_id,e))
            self.sendLine('400 FATAL ERROR 01')
            self.transport.loseConnection()
        
    def get_free_backend_id(self,checkgroup=False,startbid=None,src=None):
        global CURRENT_BACKEND_CONNS
        
        total_backends = len(CONFIG["BACKENDS"])-1
        dbg(7,"(%s) def get_free_backend_id: checkgroup = %s, startbid = %s, src = %s"%(self.conn_id,checkgroup,startbid,src))
        if self.turns > total_backends:
            dbg(7,"(%s) def get_free_backend_id: turns = %s"%(self.conn_id,self.turns))
            self.sendLine('400 NOB')
            self.transport.loseConnection()
            return
        self.turns += 1
        
        if startbid == None:
            actual_id = self.BEid
        else:
            actual_id = startbid
        
        if actual_id == -1:
            # hack to select provider on first connect
            # sback and tback define internal backend ids (not mysql dbids! see with telnet admin info!)
            # on-connect select randomly between sback:tback as primary backend providers
            # use mysql db, backend priority as latter, prio 0 (or lowest value of enabled backends) will arrive at internal id 0
            
            sback = 0 # set start backend id
            tback = 6 # set end backend id
            
            next_beid = random.randint(sback,tback)
            
            # hack to drop user to special backend
            if self.auth_user.startswith('ovpnto') or self.auth_user == 'ovpn':
                anext_beid = random.randint(7,8)
                group = GET_BACKEND_INFO(anext_beid,'GROUP')
                if group != False and (group == "tweaknews" or group == "tweaknews100m"):
                    next_beid = anext_beid
                else:
                    self.sendLine('500 NO BE')
                    dbg(1,"(%s) def get_free_backend_id: (%s) invalid backend  %s %s"%(self.conn_id,self.auth_user,anext_beid,group))
                    return self.transport.loseConnection()
        else:
            if actual_id >= total_backends:
                next_beid = 0
            else:
                next_beid = actual_id + 1
        
        backendname = CONFIG["BACKENDS"][next_beid]["NAME"]
        if backendname in DEAD_BACKENDS:
            if DEAD_BACKENDS[backendname] > int(NOW()-900):
                self.backend_jumps += 1
                return self.get_free_backend_id(checkgroup=False,startbid=next_beid,src='internal #0.0')
            else:
                del DEAD_BACKENDS[backendname]
        
        randomize = True
        try:
            if CLIENT_FACTS[self.conn_id][next_beid]['time'] > 0:
                randomize = False
        except:
            pass
        
        # randomly select different backend in same group
        try:
            if randomize == True:
                very_next_beid = next_beid + 1
                groupa = GET_BACKEND_INFO(next_beid,'GROUP')
                groupb = GET_BACKEND_INFO(very_next_beid,'GROUP')
                nameb = GET_BACKEND_INFO(very_next_beid,'NAME')
                
                free_accs = ('tweaknews1','tweaknews2')
                
                dbg(7,"(%s) def get_free_backend_id: random #0 select groupa = '%s', groupb = '%s'"%(self.conn_id,groupa,groupb))
                
                if groupa != False and groupb != False and groupa == groupb:
                    if self.auth_user.startswith('nntp') and nameb in free_accs:
                        pass
                    else:
                        dbg(7,"(%s) def get_free_backend_id: random #1 select groupa = '%s', groupb = '%s'"%(self.conn_id,groupa,groupb))
                        arand = random.randint(0,1)
                        if arand == 1:
                            dbg(7,"(%s) def get_free_backend_id: random next_beid %s, try very_next_beid %s"%(self.conn_id,next_beid,very_next_beid))
                            self.backend_jumps += 1
                            return self.get_free_backend_id(checkgroup=False,startbid=next_beid,src='internal #1.0')
                
        except Exception as e:
            dbg(1,"(%s) def get_free_backend_id: random select failed, exception = '%s'"%(self.conn_id,e))
            
        if checkgroup == True and self.bgrp == CONFIG["BACKENDS"][next_beid]["GROUP"]:
            dbg(7,"(%s) def get_free_backend_id: self.bgrp == next_beid=%s backend %s, turn"%(self.conn_id,next_beid,CONFIG["BACKENDS"][next_beid]["NAME"]))
            self.backend_jumps += 1
            return self.get_free_backend_id(checkgroup=True,startbid=next_beid,src='internal #2.1')
        else:
            if randomize == True and CURRENT_BACKEND_CONNS[next_beid] < CONFIG["BACKENDS"][next_beid]["conn"]:
                dbg(7,"(%s) def get_free_backend_id: #5.1 next_beid=%s backend %s"%(self.conn_id,next_beid,CONFIG["BACKENDS"][next_beid]["NAME"]))
                self.bgrp = CONFIG["BACKENDS"][next_beid]["GROUP"]
                self.BEid = next_beid
                self.connectionMade(connect=True,src='def conn_to_backend #5.1')
                return next_beid
            elif randomize == False:
                self.bgrp = CONFIG["BACKENDS"][next_beid]["GROUP"]
                self.BEid = next_beid
                self.connectionMade(connect=True,src='def conn_to_backend #5.2')
                return next_beid
                dbg(7,"(%s) def get_free_backend_id: #5.2 next_beid=%s backend %s"%(self.conn_id,next_beid,CONFIG["BACKENDS"][next_beid]["NAME"]))
            else:
                dbg(5,"(%s) def get_free_backend_id: backend %s %s full conns=%s/%s"%(self.conn_id,next_beid,CONFIG["BACKENDS"][next_beid]["NAME"],CURRENT_BACKEND_CONNS[next_beid],CONFIG["BACKENDS"][next_beid]["conn"]))
                return self.get_free_backend_id(checkgroup=False,startbid=next_beid,src='internal #2.2')

    def conn_to_backend(self, beid, src=None):
        global LOGBCONNS
        global CURRENT_BACKEND_CONNS
        CURRENT_BACKEND_CONNS[beid] += 1
        if not beid in LOGBCONNS:
            LOGBCONNS[beid] = {}
        LOGBCONNS[beid][self.conn_id] = str(int(NOW()))
        dbg(7,"(%s) def conn_to_backend: beid = %s, self.BEid = '%s', src = '%s'"%(self.conn_id,beid,self.BEid,src))

    def cb_user_established_conns(self, result):
        dbg(7,"(%s) Frontend def cb_user_established_conns(): result = %s"%(self.conn_id,result))
        if result == None:
            self.sendLine('400 NOSQL')
            self.transport.loseConnection()
            return
        result = int(result)
        
        if result < CONFIG["CONNS"][self.pre_auth_user]:
            dbg(7,"(%s) Frontend MYSQL_UPDATE_USER_ESTABLISHED_CONNS(): '%s'"%(self.conn_id,self.pre_auth_user))
            self.MYSQL_UPDATE_USER_ESTABLISHED_CONNS(self.pre_auth_user,self.conn_id,"up").addCallback(self.CALLBACK_UPDATE_USER_ESTABLISHED_CONNS, "up").addErrback(SQLCONNFAIL)
            # process authenticated login
        else:
            dbg(5,"(%s) Frontend MYSQL_UPDATE_USER_ESTABLISHED_CONNS(): error 502 MAXCONNS DB '%s'"%(self.conn_id,self.pre_auth_user))
            self.sendLine('502 MAXCONNS DB')
            self.transport.loseConnection()
            TEMP_BLOCK_USER(self.pre_auth_user)

    def CALLBACK_UPDATE_USER_TRAFFIC(self, result, name):
        if result != 1:
            dbg(1,"def CALLBACK_UPDATE_USER_TRAFFIC: %s error result = '%s'"%(name,result))

    def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS(self, result, direction):
        if result != None and len(result) == 2:
            if result[0] != 1:
                dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: error result[0] = '%s', result[1] = '%s'"%(self.conn_id, result[0],result[1]))
            else:
                dbg(7,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: result[0] = '%s', result[1] = '%s'"%(self.conn_id, result[0],result[1]))
            if result[0] != None:
                try:
                    if direction == "down":
                        if int(result[0]) == 1:
                            return
                        else:
                            dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: error %s '%s'"%(self.conn_id, result[0],result[1]))
                    elif direction == "up":
                        if int(result[0]) == 1:
                            try:
                                global CONN_ID
                                #if self.conn_id in CONN_ID:
                                self.auth_user = self.pre_auth_user
                                self.pre_auth_user = None
                                CONN_ID[self.conn_id]["USER"] = self.auth_user
                                dbg(1,"(%s) AUTH: '%s' %d/%d" % (self.conn_id,self.auth_user,FRONTEND_USER_CONNS[self.auth_user],CONFIG["CONNS"][self.auth_user]))
                                # pass subsequent lines to backend
                                self.waited = 0
                                self.sendLine(MSG_LOGIN)
                                self.lineReceived = self._LineToBackend
                                self.accept_request = True
                                return
                            except Exception as e:
                                dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: failed #2, exception = '%s'"%(self.conn_id,e))
                        else:
                            self.sendLine('502 LIMIT DB')
                            dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: '%s' error '502 LIMIT DB', result = '%s'"%(self.conn_id,self.pre_auth_user,result))
                            self.transport.loseConnection()
                            TEMP_BLOCK_USER(self.pre_auth_user)
                            return
                except Exception as e:
                    dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: failed #1, exception = '%s'"%(self.conn_id,e))
        self.sendLine('502 ERROR DB')
        dbg(1,"(%s) def CALLBACK_UPDATE_USER_ESTABLISHED_CONNS: '%s' error '502 ERROR (DB)', result = '%s'"%(self.conn_id,self.pre_auth_user,result))
        self.transport.loseConnection()
        TEMP_BLOCK_USER(self.pre_auth_user)
        return
    
    def MYSQL_UPDATE_USER_TRAFFIC(self, name, duration, rxbytes, notfound, articles, jumps):
        dbg(7,"(%s) def MYSQL_UPDATE_USER_TRAFFIC: '%s' dur=%s rxbytes=%s notfound=%s articles=%s jumps=%s"%(self.conn_id, name, duration, rxbytes, notfound, articles, jumps))
        try:
            return DBPOOL['USERS'].runInteraction(mysql_query_update_user_traffic, name, duration, rxbytes, notfound, articles, jumps)
        except Exception as e:
            dbg(1,"(%s) def MYSQL_UPDATE_USER_TRAFFIC: failed, exception = '%s'"%(self.conn_id,e))
            return False
    
    def MYSQL_UPDATE_USER_ESTABLISHED_CONNS(self, name, conn_id, direction):
        dbg(7,"(%s) def MYSQL_UPDATE_USER_ESTABLISHED_CONNS: '%s' %s "%(self.conn_id,name,direction))
        try:
            return DBPOOL['USERS'].runInteraction(mysql_query_update_user_established_conns, name, direction)
        except Exception as e:
            dbg(1,"(%s) def MYSQL_UPDATE_USER_ESTABLISHED_CONNS: failed, exception = '%s'"%(self.conn_id,e))
            return False
    
    def MSGID_NOTF(self):
        
        global META_CACHE
        global CONN_ID
        
        try:
            META_CACHE["NOTF"][self.msgid] = int(NOW())
            CONN_ID[self.conn_id]["NOTF"] += 1
            dbg(7,"(%s) def MSGID_NOTF: '%s' (%s)"%(self.conn_id,self.msgid,self.auth_user))
            self.msgid = None
            
        except Exception as e:
            dbg(1,"(%s) MSGID_NOTF: failed, exception = '%s'"%(self.conn_id,e))

    def f_wait_response(self, *args):
        try:
            if self.waited > 0 and self.waited < NOW()-59:
                dbg(1,"(%s) def f_wait_response(): error timeout msgid = '%s' (%s)"%(self.conn_id,self.msgid,self.auth_user))
                self.sendLine('500 RETRY')
                #self.client = None
                self.transport.loseConnection()
            else:
                
                rtt = 0
                rttr = 0
                if self.msgid_state != 0:
                    try:
                        if len(self.response_calltimes[self.BEid]) >= 5:
                            rtsum = 0
                            for rtime in self.response_calltimes[self.BEid]:
                                rtsum += rtime
                            if rtsum > 0:
                                rttr = round(rtsum / len(self.response_calltimes[self.BEid]),3)
                                rtt = round(rttr * 1.05,3)
                    except:
                        pass
                
                recalltime = 0.5
                
                if self.wait_response_rounds >= 1:
                    if self.wait_response_rounds >= 1 and self.wait_response_rounds < 5:
                        recalltime = 0.025
                    elif self.wait_response_rounds >= 5 and self.wait_response_rounds < 10:
                        recalltime = 0.050
                    elif self.wait_response_rounds >= 10 and self.wait_response_rounds < 20:
                        recalltime = 0.100
                    elif self.wait_response_rounds >= 20 and self.wait_response_rounds < 30:
                        recalltime = 0.250
                    elif self.wait_response_rounds >= 30:
                        recalltime = 1
                else:
                    if rtt == 0 or rtt >= 0.5:
                        recalltime = 0.5
                    elif rtt > 0 and rtt < 0.5:
                        recalltime = rtt
                
                self.wait_response_rounds += 1
                
                deferred = defer.Deferred()
                reactor.callLater(recalltime, deferred.callback, None)
                
                #if rtt > 0 and rtt < 0.5:
                #    dbg(9,"(%s) def f_wait_response(): return deferred, backend %s %s, rttr='%s', rtt='%s'"%(self.conn_id,self.BEid,GET_BACKEND_INFO(self.BEid,'NAME'),rttr,rtt))
                return deferred
        except Exception as e:
            dbg(1,"(%s) def f_wait_response: failed, exception = '%s'"%(self.conn_id,e))

    def f_cb_wait_response(self, *args):
        global META_CACHE
        dbg(7,"(%s) def f_cb_wait_response: #1 '%s'"%(self.conn_id,self.msgid))
        try:
            if self.conn_id not in CONN_ID:
                return
            
            if self.msgid_state != 0:
                
                dbg(7,"(%s) def f_cb_wait_response: #2 '%s'"%(self.conn_id,self.msgid))
                
                pass_line = False
                
                try:
                    
                    aSTATE = self.msgid_state
                    dbg(7,"(%s) def f_cb_wait_response: #2 '%s' aSTATE = %s"%(self.conn_id,self.msgid,aSTATE))
                    
                    if aSTATE == 9430:
                        dbg(7,"(%s) def f_cb_wait_response: '%s' STATE 9430 >> 9431"%(self.conn_id,self.msgid))
                        self.msgid_state = 9431
                        if self.client != None:
                            if self.client.jump_backend(src='def f_cb_wait_response') == True:
                                self.msgid_state = 8430
                                pass_line = True
                            else:
                                self.MSGID_NOTF()
                                pass_line = True
                        else:
                            dbg(5,"(%s) def f_cb_wait_response: error self.client=None, msgid='%s' "%(self.conn_id,self.msgid))
                            self.msgid_state = 0
                            pass_line = True
                        
                    elif aSTATE == 8430:
                        dbg(7,"(%s) def f_cb_wait_response: '%s' STATE %s"%(self.conn_id,self.msgid,aSTATE))
                        pass_line = True
                        
                    elif self.msgid in META_CACHE["NOTF"]:
                        dbg(7,"(%s) def f_cb_wait_response: '%s' STATE 430 NOTF"%(self.conn_id,self.msgid))
                        pass_line = True
                        
                except Exception as e:
                    dbg(1,"(%s) def f_cb_wait_response: failed #2, exception = '%s'"%(self.conn_id,e))
                
                try:
                    if pass_line == True and self.switch_backend == False:
                        dbg(7,"(%s) def f_cb_wait_response: '%s' _LineToBackend = '%s'"%(self.conn_id,self.msgid,self.line))
                        self.accept_request = True
                        self._LineToBackend(self.line)
                        return
                
                except Exception as e:
                    dbg(1,"(%s) def f_cb_wait_response: failed #3, exception = '%s'"%(self.conn_id,e))
                dbg(7,"(%s) def f_cb_wait_response: #3 deferred '%s' S:%s"%(self.conn_id,self.msgid,aSTATE))
                
            else:
                dbg(9,"(%s) def f_cb_wait_response: return deferred '%s'"%(self.conn_id,self.msgid))
            
            deferred = self.f_wait_response()
            return deferred.addCallback(self.f_cb_wait_response).addErrback(DEFERFAIL)
        except Exception as e:
            dbg(1,"(%s) def f_cb_wait_response: failed #0, exception = '%s'"%(self.conn_id,e))

    def f_wait_backend(self, *args):
        try:
            #if self.conn_id not in CONN_ID:
            #    dbg(1,"(%s) def f_wait_backend(): error conn_id not in CONN_ID"%(self.conn_id))
            #    return
            if self.waited > 0 and self.waited < NOW()-60:
                dbg(1,"(%s) def f_wait_backend(): error timeout msgid = '%s'"%(self.conn_id,self.msgid))
                self.transport.loseConnection()
                return
            else:
                dbg(7,"(%s) def f_wait_backend(): msgid = '%s'"%(self.conn_id,self.msgid))
                deferred = defer.Deferred()
                reactor.callLater(0.1, deferred.callback, None)
                return deferred
        except Exception as e:
            dbg(1,"(%s) def f_wait_backend: failed, exception = '%s'"%(self.conn_id,e))

    def f_cb_wait_backend(self, *args):
        if self.client == None:
            deferred = self.f_wait_backend()
            return deferred.addCallback(self.f_cb_wait_backend).addErrback(DEFERFAIL)
        else:
            self.accept_request = True
            self._LineToBackend(self.line)

    def f_auth_backend(self, *args):
        dbg(7,"(%s) def f_auth_backend(): '%s'"%(self.conn_id,self.msgid))
        deferred = defer.Deferred()
        reactor.callLater(0.1, deferred.callback, None)
        return deferred

    def f_cb_auth_backend(self, *args):
        if self.conn_id not in CONN_ID:
            dbg(1,"(%s) def f_cb_auth_backend: error self.conn_id not in CONN_ID"%(self.conn_id))
            return
        elif self.waited > 0 and self.waited < NOW()-20:
            dbg(1,"(%s) def f_cb_auth_backend: error timeout, waited = %d"%(self.conn_id,NOW()-self.waited))
            global DEAD_BACKENDS
            backendname = GET_BACKEND_INFO(self.server.BEid,'NAME')
            DEAD_BACKENDS[backendname] = int(NOW())
            self.transport.loseConnection()
            return
        
        elif CONN_ID[self.conn_id]["BACK"] == True:
            dbg(9,"(%s) def f_cb_auth_backend: authed backend %s sending line '%s'" % (self.conn_id,CONFIG["BACKENDS"][self.BEid]["NAME"],self.line))
            dbg(7,"(%s) def f_cb_auth_backend: authed backend %s sending cmd '%s'" % (self.conn_id,CONFIG["BACKENDS"][self.BEid]["NAME"],self.cmd))
            self.accept_request = True
            self._LineToBackend(self.line)
            return
        else:
            dbg(7,"%s) def f_cb_auth_backend: (waiting for backend (%s) auth, self.client = '%s', BACK = %s"%(self.conn_id,CONFIG["BACKENDS"][self.BEid]["NAME"],self.client,CONN_ID[self.conn_id]["BACK"]))
            deferred = self.f_auth_backend()
            return deferred.addCallback(self.f_cb_auth_backend).addErrback(DEFERFAIL)


class Backend(NNTPClient):
    server = None
    
    def __init__(self):
        NNTPClient.__init__(self)
        self._endState() # kill nntp's Passive state
        self._newState(self._statePassive, self._passiveError, self._headerInitial)

    def _passiveError(self, err):
        dbg(1,"(%s) def _passiveError: error '%s'"%(self.server.conn_id,str(err)))
        self.transport.loseConnection()

    def connectionMade(self):
        dbg(7,"(%s) def connectionMade: backend %s %s"%(self.server.conn_id,self.server.BEid,CONFIG["BACKENDS"][self.server.BEid]["NAME"]))
        NNTPClient.connectionMade(self)
        self.server.client = self
        if self.server.switch_backend == True:
            dbg(7,"(%s) Backend def connectionMade: send authinfo"%(self.server.conn_id))
            self.authInfo()
            self.deferred_b_auth_backend = self.b_auth_backend()
            self.deferred_b_auth_backend.addCallback(self.b_cb_auth_backend).addErrback(DEFERFAIL)
            return

    def b_auth_backend(self, *args):
        dbg(7,"(%s) def b_auth_backend: wait auth backend %s %s"%(self.server.conn_id,self.server.BEid,CONFIG["BACKENDS"][self.server.BEid]["NAME"]))
        deferred = defer.Deferred()
        reactor.callLater(0.1, deferred.callback, None)
        return deferred

    def b_cb_auth_backend(self, *args):
        if self.server.conn_id in CONN_ID:
            
            if CONN_ID[self.server.conn_id]["BACK"] == True:
                dbg(7,"(%s) def b_cb_auth_backend: authed backend %s"%(self.server.conn_id,CONFIG["BACKENDS"][self.server.BEid]["NAME"]))
                return
            else:
                dbg(7,"(%s) def b_cb_auth_backend: waiting auth backend %s"%(self.server.conn_id,CONFIG["BACKENDS"][self.server.BEid]["NAME"]))
                deferred = self.b_auth_backend()
                return deferred.addCallback(self.b_cb_auth_backend).addErrback(DEFERFAIL)
        else:
            dbg(7,"(%s) def b_cb_auth_backend: not in CONN_ID, loseConnection backend %s"%(self.server.conn_id,CONFIG["BACKENDS"][self.server.BEid]["NAME"]))
            self.transport.loseConnection()
            return

    def authInfo(self):
        self.sendLine('AUTHINFO USER ' + CONFIG["BACKENDS"][self.server.BEid]["user"])
        self._newState(None, self.authInfoFailed, self._authInfoUserResponse)

    def _authInfoUserResponse(self, (code, message)):
        if code == 381:
            self.sendLine('AUTHINFO PASS ' + CONFIG["BACKENDS"][self.server.BEid]["pass"])
            self._newState(None, self.authInfoFailed, self._authInfoPassResponse)
        else:
            self.authInfoFailed('%d %s' % (code, message))
        self._endState()

    def _authInfoPassResponse(self, (code, message)):
        if code == 281:
            self.gotAuthInfoOk('%d %s' % (code, message))
        
        else:
            self.authInfoFailed('%d %s' % (code, message))
        self._endState()

    def gotAuthInfoOk(self, message):
        global CONN_ID
        backendname = GET_BACKEND_INFO(self.server.BEid,'NAME')
        dbg(3,"(%s) def gotAuthInfoOk: backend %s %s '%s' (%s)"%(self.server.conn_id,self.server.BEid,backendname,str(message),self.server.auth_user))
        CONN_ID[self.server.conn_id]["BACK"] = True
        
        if CONN_ID[self.server.conn_id]["JUMP"] == 0:
            CONN_ID[self.server.conn_id]["INIT"] = self.server.BEid
            CONN_ID[self.server.conn_id]["JUMP"] += 1
        
        try:
            global BESTATS
            bid = GET_BACKEND_INFO(self.server.BEid,'BID')
            BESTATS[bid]['choosen_local'] += 1
        except Exception as e:
            dbg(1,"(%s) def gotAuthInfoOk: BESTATS failed, exception = '%s'"%(self.server.conn_id,e))
        
        self.server.switch_backend = False

    def authInfoFailed(self, err):
        backendname = GET_BACKEND_INFO(self.server.BEid,'NAME')
        dbg(1,"(%s) def authInfoFailed: backend %s %s error '%s' (%s)"%(self.server.conn_id,self.server.BEid,backendname,str(err),self.server.auth_user))
        
        global CONN_ID
        global DEAD_BACKENDS
        
        DEAD_BACKENDS[backendname] = int(NOW())
        
        try:
            self.deferred_b_auth_backend.cancel()
        except:
            pass
        
        try:
            global BESTATS
            bid = GET_BACKEND_INFO(self.server.BEid,'BID')
            BESTATS[bid]['failure_local'] += 1
        except Exception as e:
            dbg(1,"(%s) def gotAuthInfoOk: BESTATS failed, exception = '%s'"%(self.server.conn_id,e))
        
        
        try:
            self.jump_backend(src='def authInfoFailed')
        except Exception as e:
            dbg(1,"(%s) def authInfoFailed: jump_backend() failed, exception = '%s'"%(self.server.conn_id,e))

    def connectionLost(self, reason):
        reason = clean_REASON(reason)
        dbg(9,"(%s) Backend: def connectionLost: %s" % (self.server.conn_id,reason))

    def connectionFailed(self, reason):
        reason = clean_REASON(reason)
        dbg(1,"(%s) Backend: def connectionFailed: %s" % (self.server.conn_id,reason))

    def gotHead(self, article):
        return self.gotArticle(article)

    def getHeadFailed(self, error):
        return self.getArticleFailed(error)

    def gotBody(self, article):
        return self.gotArticle(article)

    def getBodyFailed(self, error):
        return self.getArticleFailed(error)
    
    def gotOverview(self, overview):
        dbg(5,"(%s) def gotOverview: len=%s overview='%s' (%s)"%(self.server.conn_id,len(overview),str(overview),self.server.auth_user))
        #self._gotOverview(overview)
        
    def _gotOverview(self, parts):
        self.server.sendLine('215 Order of fields in overview database.')
        self.server.sendLine('.')
        
    def gotAllGroups(self, groups):
        
        #dbg(5,"(%s) def gotAllGroups: groups='%s'"%(self.server.conn_id,groups))
        #return
        
        global NEWSGROUPS_LIST
        
        
        self.server.sendLine('215 LIST FOLLOWS')
        
        # prefilter
        for group in groups:
            if group[0].startswith('alt.bin'):
                NEWSGROUPS_LIST.append(group)
                string = "%s %s %s %s" % (group[0],group[1],group[2],group[3])
                self.server.sendLine(string,log=False)
        return self.server.sendLine('.')
        
        
        if not len(NEWSGROUPS_LIST):
            groupnames = list()
            active_file = 'list_%s.txt' % (GET_BACKEND_INFO(self.server.BEid,'GROUP'))
            write_file = False
            if not os.path.isfile(active_file):
                try:
                    fp = open(active_file, "wb")
                    write_file = True
                except:
                    pass
                
                for group in groups:
                    if group[0].startswith('alt.bin'):
                        string = "%s %s %s %s" % (group[0],group[1],group[2],group[3])
                        self.server.sendLine(string,log=False)
                        NEWSGROUPS_LIST.append(group)
                        if write_file == True:
                            fp.write(group[0]+'\r\n')
                fp.close()
                
            try:
                file = 'list_names_%s.txt' % (GET_BACKEND_INFO(self.server.BEid,'GROUP'))
                if not os.path.isfile(file):
                    fp = open(file, "wb")
                    for groupname in groupnames:
                        fp.write(groupname+'\r\n')
                    fp.close()
            except Exception as e:
                dbg(1,"(%s) def gotAllGroups: failed write, exception = '%s'"%(self.server.conn_id,e))            
            
        else:
            for group in NEWSGROUPS_LIST:
                string = "%s %s %s %s" % (group[0],group[1],group[2],group[3])
                self.server.sendLine(string,log=False)
        
        self.server.sendLine('.')
        
        dbg(5,"(%s) def gotAllGroups: len(groups)=%s"%(self.server.conn_id,len(groups)))
        return

        
        #self.server.sendLine('502 LIST GOT GROUPS %s'%(len(groups)))
    
    def getAllGroupsFailed(self, error):
        dbg(5,"(%s) def getAllGroupsFailed: error = '%s'"%(self.server.conn_id,str(error)))
        self.server.sendLine('502 LIST FAILED')
    
    def gotArticle(self, article):
        global CONN_ID
        global META_CACHE
        
        try:
            self.server.deferred_wait_response.cancel()
            dbg(9,"def gotArticle: self.server.deferred.cancel() OK, ignore Failure error")
        except Exception as e:
            dbg(9,"def gotArticle: self.server.deferred.cancel() failed, exception = '%s'"%(e))
        
        try:
            if len(self.response_calltimes):
                self.response_calltimes = dict()
        except:
            pass
        
        META_CACHE["SENT"] += 1
        tbytes = self.server.gotBytes
        
        try:
            global BESTATS
            bid = GET_BACKEND_INFO(self.server.BEid,'BID')
            BESTATS[bid]['rxbytes_local'] += tbytes
            BESTATS[bid]['article_local'] += 1
        except Exception as e:
            dbg(1,"(%s) def gotArticle: BESTATS failed, exception = '%s'"%(self.server.conn_id,e))
        
        
        try:
            test = CONN_ID[self.server.conn_id]
            del test
        except:
            return
        
        CONN_ID[self.server.conn_id]["ARTS"] += 1
        CONN_ID[self.server.conn_id]["RX_BYTES"] += tbytes
        
        backendname = GET_BACKEND_INFO(self.server.BEid,'NAME')
        
        try:
            runtime = get_runtime(self.server.waited)
            speed = int(tbytes / runtime / 1024)
        except Exception as e:
            dbg(1,"(%s) def gotArticle: failed runtime, exception = '%s'"%(self.server.conn_id,e))
            runtime = 0
            speed = 0
        
        self.server.notfound_count = 0
        self.server.waited = 0
        self.server.wait_sum += runtime
        self.server.gotBytes = 0
        self.server.response_rounds = 0
        
        try:
            rx_bytes = CONN_ID[self.server.conn_id]["RX_BYTES"]
            duration_total = int(NOW() - self.server.conn_start)
            speed_total = int(rx_bytes / duration_total / 1024)
        except:
            speed_total = 0
        
        if DEBUG_LEVEL == 5: dbg(5,"(%s) def gotArticle: tbytes=%s kbs=%s rt=%s cmd='%s' be=%s (%s) @AVG %d KBs"%(self.server.conn_id,tbytes,speed,runtime,self.server.cmd,backendname,self.server.auth_user,speed_total))
        elif DEBUG_LEVEL > 5: dbg(7,"(%s) def gotArticle: '%s' tbytes=%s kbs=%s rt=%s cmd='%s' be=%s (%s) @AVG %d KBs"%(self.server.conn_id,self.server.msgid,tbytes,speed,runtime,self.server.cmd,backendname,self.server.auth_user,speed_total))
        
        self.server.msgid = None
        self.server.accept_request = True
        #self.transport.resumeProducing()
        return
    
    def getArticleFailed(self, error):
        runtime = get_runtime(self.server.waited)
        self.server.waited = 0
        self.server.msgid_state = 9430
        self.server.notfound_count += 1
        response_rounds = self.server.wait_response_rounds
        
        
        try:
            if len(self.server.response_calltimes[self.server.BEid]) > 20:
                self.server.response_calltimes[self.server.BEid].pop(0)
        except:
            self.server.response_calltimes[self.server.BEid] = list()
        self.server.response_calltimes[self.server.BEid].append(runtime)
        
        try:
            global BESTATS
            bid = GET_BACKEND_INFO(self.server.BEid,'BID')
            BESTATS[bid]['nofound_local'] += 1
        except Exception as e:
            dbg(1,"(%s) def getArticleFailed: BESTATS failed, exception = '%s'"%(self.server.conn_id,e))
        
        #dbg(7,"(%s) def getArticleFailed: '%s' msg='%s', rt=%s, count %d, backend %s %s (%s), response_rounds = '%s'"%(self.server.conn_id,self.server.msgid,str(error),runtime,self.server.notfound_count,self.server.BEid,GET_BACKEND_INFO(self.server.BEid,'NAME'),self.server.auth_user,response_rounds))
        if self.server.wait_response_rounds > 1:
            dbg(5,"(%s) def getArticleFailed: '%s', rt=%s, count %d, backend %s %s (%s), response_rounds = '%s'"%(self.server.conn_id,self.server.msgid,runtime,self.server.notfound_count,self.server.BEid,GET_BACKEND_INFO(self.server.BEid,'NAME'),self.server.auth_user,response_rounds))
        
        self.server.accept_request = True
        self.server.wait_response_rounds = 0
        
        #fuckhere
        dbg(5,"(%s) def getArticleFailed: sent quit" %(self.server.conn_id))
        #self.transport.loseConnection()
        self.sendLine("quit")
        dbg(5,"(%s) def getArticleFailed: set CLIENT_FACTS[Beid] = None" %(self.server.conn_id))
        CLIENT_FACTS[self.server.conn_id][self.server.BEid]['prot'] = None
        return
    
    def _stateArticle(self, line):
        self.server.sendLine(line)
        self.server.gotBytes += len(line)
        if line != '.':
            self._newLine(line, 0)
        else:
            self.gotArticle(self._endState())
    
    def _stateHead(self, line):
        self.server.sendLine(line)
        self.server.gotBytes += len(line)
        
        if line != '.':
            self._newLine(line, 0)
        else:
            self.gotArticle(self._endState())
    
    def _stateBody(self, line):
        self.server.sendLine(line)
        self.server.gotBytes += len(line)
        if line != '.':
            self._newLine(line, 0)
        else:
            self.gotArticle(self._endState())
    
    def sendLine(self, line):
        #dbg(9,"(%s) def sendLine: '%s'"%(self.server.conn_id,line))
        self.transport.write(line + self.delimiter)
    
    def fetchHead(self, index = ''):
        dbg(7,"(%s) def fetchHead() index = '%s'"%(self.server.conn_id,index))
        self.sendLine('HEAD %s' % (index,))
        self._newState(self._stateHead, self.getHeadFailed)
    
    def fetchBody(self, index = ''):
        dbg(7,"(%s) def fetchBody() index = '%s'"%(self.server.conn_id,index))
        self.sendLine('BODY %s' % (index,))
        self._newState(self._stateBody, self.getBodyFailed)
    
    def fetchArticle(self, index = ''):
        dbg(7,"(%s) def fetchArticle() index = '%s'"%(self.server.conn_id,index))
        self.sendLine('ARTICLE %s' % (index,))
        self._newState(self._stateArticle, self.getArticleFailed)
    
    def lineReceived(self, line):
        # called everytime we received a line from backend
        #dbg(9,"Backend def lineReceived: '%s'"%(line))
        
        #global CONN_ID
        #global META_CACHE
        
        code = None
        try:
            """ cut & paste from hellanzb NNTPClient """
            if not len(self._state):
                self._statePassive(line)
                dbg(5,"(%s) B: def lR: _statePassive '%s'"%(self.server.conn_id,self.server.msgid))
            
            elif self._getResponseCode() is None:
                code = extractCode(line)
                #dbg(5,"(%s) B: def lR: _getResponseCode #1 is None, line='%s' code='%s' "%(self.server.conn_id,line,code))
                if code is None or (not (200 <= code[0] < 400) and code[0] != 100):    # An error!
                    #dbg(5,"(%s) B: def lR: _getResponseCode #2 is None or, line='%s'"%(self.server.conn_id,line))
                    try:
                        self._error[0](line)
                    except Exception as e:
                        dbg(1,"(%s) B: def lR: failed #1, exception = '%s'"%(self.server.conn_id,e))
                    
                    self._endState()
                else:
                    
                    self._setResponseCode(code)
                    if self._responseHandlers[0]:
                        self._responseHandlers[0](code)
                    
                    #dbg(5,"(%s) B: def lR: _getResponseCode else #3 '%s' code='%s' to front"%(self.server.conn_id,line,code))
                    
                    sendline = False
                    
                    if self.server.cmd == 'ARTICLE' and code[0] == 220:
                        sendline = True
                    elif self.server.cmd == 'BODY' and code[0] == 222:
                        sendline = True
                    elif self.server.cmd == 'HEAD' and code[0] == 221:
                        sendline = True
                    elif self.server.cmd == 'LIST_OVERVIEW' and code[0] == 215:
                        sendline = True
                    #elif self.server.cmd == 'LIST' and code[0] == 215:
                    #    sendline = True
                    
                    if sendline == True:
                        self.server.sendLine(line)
            else:
                #dbg(9,"(%s) B: def lR: _state[0](line) '%s'"%(self.server.conn_id,self.server.msgid))
                #self.server.sendLine(line)
                if self.server.cmd == 'LIST_OVERVIEW':
                        self.server.sendLine(line)
                    # filter-out XREF line from header
                    #if not line.upper().startswith('XREF'):
                    #    self.server.sendLine(line)
                elif self.server.cmd == 'LIST':
                    self.server.sendLine(line)
                self._state[0](line)
        except Exception as e:
            dbg(1,"(%s) B: def lR: failed #0, exception = '%s'"%(self.server.conn_id,e))

    def jump_backend(self,src=None):
        global CURRENT_BACKEND_CONNS
        global CONN_ID
        
        # hack to deny ovpn:free from jumping, because we forced backends
        if self.server.auth_user.startswith('ovpnto') or self.server.auth_user == 'ovpn':
            return False
        
        dbg(7,"(%s) def jump_backend: src = '%s'"%(self.server.conn_id,src))
        
        try:
        
            if self.server.backend_jumps >= len(CONFIG["BACKENDS"])-1:
                dbg(7,"(%s) def jump_backend: self.backend_jumps = '%d'"%(self.server.conn_id,self.server.backend_jumps))
                self.server.backend_jumps = 0
                self.server.notfound_count = 0
                self.server.switch_backend = False
                return False
            
            self.server.switch_backend = True
            self.server.backend_jumps += 1
            CONN_ID[self.server.conn_id]["JUMP"] += 1
            CONN_ID[self.server.conn_id]["BACK"] = False
            
            self.server.BEid = self.server.get_free_backend_id(checkgroup=True,src='def jump_backend')
            
            self.server.turns = 0
            return True
        except Exception as e:
            dbg(1,"(%s) def jump_backend: failed, exception = '%s'"%(self.server.conn_id,e))


class BackendFactory(ClientFactory):
    server = None
    protocol = Backend
    
    def buildProtocol(self, *args, **kw):
        dbg(7,"(%s) BackendFactory: buildProtocol()"%(self.server.conn_id))
        
        try:
            global CLIENT_FACTS
            global CONN_ID
            
            if CLIENT_FACTS[self.server.conn_id][self.server.BEid]['prot'] != None:
                
                prot = CLIENT_FACTS[self.server.conn_id][self.server.BEid]['prot'] # represents a backend instance
                prot.server = self.server # represents the frontend instance
                
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['last'] = int(NOW())
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['used'] += 1
                CONN_ID[self.server.conn_id]["BACK"] = True
                self.server.switch_backend = False
                
                dbg(7,"(%s) BackendFactory: USE prot = '%s', linkedto '%s'"%(self.server.conn_id,prot,prot.server))
                return prot
            
            else:
                prot = ClientFactory.buildProtocol(self, *args, **kw) # represents a backend instance
                prot.server = self.server # represents the frontend instance
                
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['prot'] = prot
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['time'] = int(NOW())
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['used'] = 1
                CLIENT_FACTS[self.server.conn_id][self.server.BEid]['last'] = int(NOW())
                
                dbg(7,"(%s) BackendFactory: NEW prot = '%s', linkto '%s'"%(self.server.conn_id,prot,prot.server))
                
                try:
                    MYSQL_SET_BACKEND_SESSION(self.server.conn_id, GET_BACKEND_INFO(self.server.BEid,'BID'), self.server.auth_user, "up", src='BE_FACT_NEW').addCallback(CALLBACK_SET_BACKEND_SESSION, "up", self.server.conn_id).addErrback(SQLCONNFAIL)
                except Exception as e:
                    dbg(1,"(%s) MYSQL_SET_BACKEND_SESSION failed #0, exception = '%s'"%(self.server.conn_id,e))
                
                self.server.conn_to_backend(self.server.BEid, src='buildProtocol')
                return prot
                
        except Exception as e:
            dbg(5,"(X) BackendFactory: __init__() failed, exception = '%s'"%e)

    def startedConnecting(self, connector):
        try:
            dbg(7,"(%s) BackendFactory: startedConnecting() backend %s %s"%(self.server.conn_id,self.server.BEid,GET_BACKEND_INFO(self.server.BEid,"NAME")))
        except Exception as e:
            dbg(1,"(%s) BackendFactory: startedConnecting failed, exception = '%s'"%(self.server.conn_id,e))

    def remove_BConn(self,beid):
        global LOGBCONNS
        global CURRENT_BACKEND_CONNS
        dbg(7,"(%s) BackendFactory: def remove_BConn: beid = %s"%(self.server.conn_id,beid))
        CURRENT_BACKEND_CONNS[beid] = max(0, CURRENT_BACKEND_CONNS[beid] - 1)
        
        try:
            MYSQL_SET_BACKEND_SESSION(self.server.conn_id, GET_BACKEND_INFO(beid,'BID'), self.server.auth_user, "down", src='remove_BConn').addCallback(CALLBACK_SET_BACKEND_SESSION, "up", self.server.conn_id).addErrback(SQLCONNFAIL)
        except Exception as e:
            dbg(1,"(%s) MYSQL_SET_BACKEND_SESSION failed #1, exception = '%s'"%(self.server.conn_id,e))
        
        
        if beid in LOGBCONNS:
            if self.server.conn_id in LOGBCONNS[beid]:
                del LOGBCONNS[beid][self.server.conn_id]
                dbg(5,"(%s) BackendFactory: def remove_BConn: disconnected beid = '%s'"%(self.server.conn_id,beid))
        
        try:
            self.server.client.deferred_b_auth_backend.cancel()
            dbg(7,"(%s) BackendFactory: def remove_BConn: self.client.deferred_b_auth_backend.cancel()"%(self.server.conn_id))
        except Exception as e:
            dbg(7,"(%s) BackendFactory: def remove_BConn: self.client.deferred_b_auth_backend.cancel() failed, exception = '%s'"%(self.server.conn_id,e))
        
    def clientConnectionLost(self, connector, reason, src=None):
        dbg(7,"(%s) BackendFactory: clientConnectionLost() '%s', src = '%s', connector = '%s'"%(self.server.conn_id,clean_REASON(reason),src,connector))
        
        global CONN_ID
        global CLIENT_FACTS
        
        remove_beid = None
        
        try:
            if self.server.conn_id in CONN_ID:
                
                for beid,conn in CONN_ID[self.server.conn_id]["TCPC"].viewitems():
                    if conn == connector:
                        remove_beid = beid
                
                if remove_beid != None:
                    try: del CONN_ID[self.server.conn_id]["TCPC"][remove_beid]
                    except: pass
                    
                    try: del CLIENT_FACTS[self.server.conn_id][remove_beid]
                    except: pass
                    
                    try: self.remove_BConn(remove_beid)
                    except: pass
                    dbg(7,"(%s) BackendFactory: clientConnectionLost() remove_beid = '%s'"%(self.server.conn_id,remove_beid))
        except Exception as e:
            dbg(1,"(%s) BackendFactory: clientConnectionLost() #1 failed, exception = '%s'"%(self.server.conn_id,e))
        
        try:
            
            if remove_beid == self.server.BEid:
                self.server.BEid = -1
                self.server.client = None
                CONN_ID[self.server.conn_id]["BACK"] = False
                dbg(1,"(%s) BackendFactory: clientConnectionLost() SETBACK = False"%(self.server.conn_id))
                
        except Exception as e:
            dbg(1,"(%s) BackendFactory: clientConnectionLost() #2 failed, exception = '%s'"%(self.server.conn_id,e))

    def clientConnectionFailed(self, connector, reason):
        global CONN_ID
        global DEAD_BACKENDS
        DEAD_BACKENDS[GET_BACKEND_INFO(self.server.BEid,"NAME")] = int(NOW())
        
        dbg(1,"(%s) BackendFactory: clientConnectionFailed() error backend %s %s %s"%(self.server.conn_id,self.server.BEid,GET_BACKEND_INFO(self.server.BEid,"NAME"),clean_REASON(reason)))
        self.clientConnectionLost(connector, clean_REASON(reason), src='def clientConnectionFailed')
        self.server.msgid_state = 9430

# startup
try:
    IDLE_TIMER()
    while CONFIG == False:
        print("loading config")
        time.sleep(1)
except Exception as e:
    dbg(1,"CONFIG failed, exception = '%s'"%e)
    
try:
    if CONFIG["FRONTEND"]["LOGS"] == True:
        LOGFILE = CONFIG["FRONTEND"]["LOGF"]
        if os.path.isfile(LOGFILE):
            try:
                os.remove(LOGFILE)
            except:
                print("logfile remove failed: %s")
                sys.exit(1)
        log.startLogging(file(LOGFILE, 'a'))
except Exception as e:
    dbg(1,"LOGFILE failed, exception = '%s'"%e)

print("sys.platform = '%s'"%sys.platform)

try:
    """
    if sys.platform == "linux2":
        try:
            # fixme: experimental import of epollreactor
            from twisted.internet import epollreactor
            #try:
            #    epollreactor.install()
            #except Exception as e:
            #    dbg(1,"epollreactor.install() failed, exception = '%s'"%e)
        except Exception as e:
            dbg(1,"import epollreactor failed, exception = '%s'"%e)
    """
    from twisted.internet import reactor
    
    FrontendFactory = ServerFactory()
    FrontendFactory.protocol = Frontend
    FrontendFactory.protocol.clientFactory = BackendFactory
    
    reactor.listenTCP(CONFIG["LPORT"], FrontendFactory, interface=CONFIG["LHOST"])
    reactor.run()
except Exception as e:
    dbg(1,"Factory or Reactor failed, exception = '%s'"%e)
    sys.exit()
