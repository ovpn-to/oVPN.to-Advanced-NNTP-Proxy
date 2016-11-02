#!/usr/bin/python2 -O
#
#	oVPN.to Advanced NNTP Proxy - Version: 0.1 (PUBLIC) 
#
#	Thanks to:
#		ddeus@sourceforge for basic ideas
#
#	Copyright: oVPN.to Anonymous Services
#

def sample_config():
	sys.stdout.write("\nExample nntp.conf:\n\n")
	sys.stdout.write("""[frontend]
bindhost = 127.0.0.1			# set listen address
bindport = 11119				# set listen port
userfile = user.conf			# read users from file
cfg_read = 300					# reload config every x seconds

[backend]
host = nntp.example.com
port = 119
user = myuser
pass = mypass
conn = 50
tout = 15
logs = False
logf = nntp.log
pidf = nntp.pid
call = 0
""")
	sys.stdout.write("\nExample user.conf:\n\n")
	sys.stdout.write("""[users]
ovpn = ad95d5fa651ba86d8923fe1238d24a4f1988a752acfe426ac72ac7c04471bc17
[conns]
ovpn = 50
""")
	sys.exit(1)

import getpass, hashlib, os, psutil, random, requests, sys, time, threading, zlib
from twisted.internet import reactor
from twisted.internet.protocol import ServerFactory, ClientFactory, Factory
from twisted.protocols.basic import LineReceiver
from twisted.python import log
from ConfigParser import SafeConfigParser
Factory.noisy = False

#GLOBALS: BOOL
ALLOW_MODE_READER = True	# allows anonymous newsreader connection

# GLOBALS: INT
RELOAD_CONFIG_EVERY = 300			# RELOAD CONFIG
MAX_TEMP_CONNS = 10					# FRONTEND LIMIT DROP TEMP NOAUTH CONNS
MAX_TEMP_IDLE_TIME = 10				# FRONTEND DISCONNECT IDLE NOAUTH CONNS
MAX_CONN_IDLE_TIME = 300			# FRONTEND DISCONNECT IDLE AUTHED CONNS
MAX_READER_CONNS = 1				# SET MAX ANONYMOUS READER CONNECTIONS

# GLOBALS: DICT
CONN_ID = {}						# stores frontend conn_id infos
FRONTEND_USER_CONNS = {}			# stores frontend established connections to provide conn limits
USER_CONNECTIONS = {}				# stores content of users.conf
USER_TRAFFIC = {}					# stores user traffic values
LAST_ACTIONS = {}					# stores last runtime of internal jobs
LAST_ACTIONS["CFG_RELOAD"] = 0

# GLOBALS: ZERO
CURRENT_BACKEND_CONNS = 0			# zero!
CURRENT_TEMP_CONNS = 0				# zero!
CURRENT_READER_CONNS = 0			# zero!

# GLOBALS: FALSE
UPDATE_TRAFFICDB_RUNNING = False	# false!
IDLE_TIMER_RUNNING = False			# false!
CONFIG = False						# false!

def dbg(lvl,msg):
	DEBUGLEVEL = 1
	if lvl <= DEBUGLEVEL:
		print(msg)

def memory_usage_psutil():
	try:
		# return the memory usage in MB
		process = psutil.Process(os.getpid())
		#print("process = '%s'"%(process))
		mem = process.get_memory_info()[0] / float(2 ** 20)
		return mem
	except:
		return 0


def lineisascii(line):
	try:
		line.decode('ascii')
		return True
	except UnicodeDecodeError:
		return False

def read_config():
	global RELOAD_CONFIG_EVERY
	if len(sys.argv) > 1 and len(sys.argv[1]) > 1:
		maincfg = sys.argv[1]
		if os.path.isfile(maincfg):
			dbg(9,"def read_config: reading maincfg '%s'"%(maincfg))
			main_cfg = SafeConfigParser()
			main_cfg.read(maincfg)
			
			# read frontend section
			if main_cfg.has_option('frontend', 'bindhost') and main_cfg.has_option('frontend', 'bindport') and main_cfg.has_option('frontend', 'cfg_read'):
				LHOST = main_cfg.get('frontend', 'bindhost')
				LPORT = main_cfg.getint('frontend', 'bindport')
				RELOAD_CONFIG_EVERY = main_cfg.getint('frontend', 'cfg_read')
				if LPORT <= 1024:
					LPORT = 11119
			
			# read backend section
			if main_cfg.has_section('backend'):
				tmpcfg = dict()
				options = ( 'host', 'port', 'user', 'pass', 'conn', 'call', 'tout', 'logs', 'logf' )
				for opt in options:
					if main_cfg.has_option('backend', opt):
						if opt == 'conn' or opt == 'port' or opt == 'call' or opt == 'tout':
							tmpcfg[opt] = main_cfg.getint('backend', opt)
						elif opt == 'logs':
							tmpcfg[opt] = main_cfg.getboolean('backend', opt)
						else:
							tmpcfg[opt] = main_cfg.get('backend', opt)
					else:
						dbg(1,"def read_config: backend missing option '%s'  '%s'"%(opt))
						return False
					dbg(9,"tmpcfg[%s]:%s"%(opt,tmpcfg[opt]))
				if len(tmpcfg) == len(options):
					BACKEND = tmpcfg
					BACKEND["OFFLINE"] = False
				else:
					return False
			else:
				dbg(1,"def read_config: main_cfg missing section 'backend'")
				return False
			
			# read users
			if main_cfg.has_option('frontend', 'userfile'):
				USERS = dict()
				CONNS = dict()
				ucfg = main_cfg.get('frontend', 'userfile')
				if os.path.isfile(ucfg):
					dbg(9,"def read_config: ucfg '%s'"%(ucfg))
					u_cfg = SafeConfigParser()
					u_cfg.read(ucfg)
					if u_cfg.has_section('users') and u_cfg.has_section('conns'):
						tUSERS = dict(u_cfg.items('users'))
						tCONNS = dict([(x, int(y)) for x,y in u_cfg.items('conns')])  
						if len(tUSERS) == len(tCONNS):
							USERS = tUSERS
							CONNS = tCONNS
						else:
							dbg(1,"def read_config: userfile '%s' failed"%(ucfg))
							return False
					
					dbg(9,"tmpcfg[%s]:%s"%(opt,tmpcfg[opt]))
				else:
					dbg(1,"def read_config: userfile '%s' not found"%(ucfg))
					return False
			else:
				dbg(1,"def read_config: userfile option missing")
				return False
		else:
			dbg(1,"configfile not found")
			return False
	else:
		dbg(1,"configfile not defined")
		return False
	dbg(2,"def read_config: loaded backend '%s'"%(BACKEND))
	dbg(2,"def read_config: loaded users '%s'"%(len(tUSERS)))
	return { 'BACKEND':BACKEND, 'CONNS':CONNS, 'LHOST':LHOST, 'LPORT':LPORT, 'USERS':USERS }

def CONFIG_LOAD():
	global CONFIG
	global LAST_ACTIONS
	cfg = read_config()
	if cfg == False:
		LAST_ACTIONS["CFG_RELOAD"] = int(time.time()+30-RELOAD_CONFIG_EVERY)
		dbg(1,"def CONFIG_LOAD(): failed, retry in 30 sec")
	else:
		CONFIG = cfg
		LAST_ACTIONS["CFG_RELOAD"] = int(time.time())
		dbg(1,"def CONFIG_LOAD(): OK")

def IDLE_TIMER():
	global IDLE_TIMER_RUNNING
	global CONN_ID
	now = time.time()
	if IDLE_TIMER_RUNNING == True:
		#print("mem = '%s'"%(memory_usage_psutil()))
		
		# check for CFG_RELOAD
		if LAST_ACTIONS["CFG_RELOAD"] < (now-RELOAD_CONFIG_EVERY):
			CONFIG_LOAD()
		
		# kill idle connections
		for cid,val in CONN_ID.items():
			kill = False
			if val["AUTH"] == False and int(time.time()-val["TIME"]) > MAX_TEMP_IDLE_TIME:
				kill = True
				dbg(1,"def IDLE_TIMER: killed non-auth idle conn_id = '%s'"%(cid))
			elif val["AUTH"] == True and int(time.time()-val["LAST"]) > MAX_CONN_IDLE_TIME:
				kill = True
				dbg(1,"def IDLE_TIMER: killed auth idle conn_id = '%s'"%(cid))
			if kill == True:
				val["TCPC"].disconnect()
		# sleep a second before we run again
		time.sleep(0.1)
	end = time.time()
	IDLE_TIMER_RUNNING = True
	thread = threading.Thread(name='IDLE_TIMER',target=IDLE_TIMER)
	thread.start()

def msgid_format(msgid):
	if msgid.startswith('<') and msgid.endswith('>'):
		return True

class Frontend(LineReceiver):
	auth_user = None
	clientFactory = None
	client = None
	transport = None
	
	def makeConnection(self, transport):
		global CURRENT_TEMP_CONNS
		global CONN_ID
		self.remove_conn_on_lost = True
		self.transport = transport
		self.conn_start = int(time.time())
		
		self.msgid = None
		self.msgid_fb = None
		
		self.conn_id = "%s-%s" % (self.conn_start,random.randint(1,(2**16)))
		CONN_ID[self.conn_id] = {}
		CONN_ID[self.conn_id]["TIME"] = self.conn_start	# frontend session start time
		CONN_ID[self.conn_id]["TCPC"] = None			# frontend conn has no reactor
		CONN_ID[self.conn_id]["USER"] = None			# frontend conn has no user
		CONN_ID[self.conn_id]["BACK"] = False			# frontend conn has no backend
		CONN_ID[self.conn_id]["AUTH"] = False			# frontend conn is not authed
		CONN_ID[self.conn_id]["TRAF"] = {}				# frontent conn has no traffic
		CONN_ID[self.conn_id]["TRAF"]["RX_BYTES"] = 0	# zero!
		CONN_ID[self.conn_id]["TRAF"]["RX_LINES"] = 0	# zero!
		CONN_ID[self.conn_id]["TRAF"]["TX_BYTES"] = 0	# zero!
		CONN_ID[self.conn_id]["TRAF"]["TX_LINES"] = 0	# zero!
		CONN_ID[self.conn_id]["LAST"] = 0				# zero!
		
		if CURRENT_TEMP_CONNS > MAX_TEMP_CONNS:
			self.sendLine('400 RETRY LATER')
			self.remove_conn_on_lost = False
			dbg(1,"def makeConnection: CURRENT_TEMP_CONNS REACHED")
			return self.transport.loseConnection()
		elif CURRENT_BACKEND_CONNS > CONFIG["BACKEND"]["conn"]:
			self.sendLine('502 RETRY LATER')
			self.remove_conn_on_lost = False
			dbg(1,"def makeConnection: CURRENT_BACKEND_CONNS REACHED")
			return self.transport.loseConnection()
		elif CONFIG["BACKEND"]["OFFLINE"] == True:
			self.sendLine('502 BACKEND ERROR')
			self.remove_conn_on_lost = False
			dbg(1,"def makeConnection: OFFLINE, BACKEND ERROR")
		else:
			dbg(911,"def makeConnection() peer = '%s', conn_id = '%s'"%(self.transport.getPeer(),self.conn_id))
			dbg(5,"F_NEW: (.)(cid='%s')"%(self.conn_id))
			CURRENT_TEMP_CONNS += 1
			self.connectionMade()
	
	def connectionMade(self):
		self.transport.pauseProducing()
		client = self.clientFactory()
		client.server = self
		CONN_ID[self.conn_id]["TCPC"] = reactor.connectTCP(CONFIG["BACKEND"]["host"], CONFIG["BACKEND"]["port"], client, timeout=CONFIG["BACKEND"]["tout"])
		dbg(5,"Frontend connectionMade: CONN_ID[self.conn_id] = '%s'"%(CONN_ID[self.conn_id]))

	def connectionLost(self, reason):
		dbg(5,"Frontend connectionLost: %s"%(reason))
		global CURRENT_BACKEND_CONNS
		global FRONTEND_USER_CONNS
		global CURRENT_TEMP_CONNS
		global CURRENT_READER_CONNS
		global CONN_ID
		
		if self.client is not None:
			self.client.transport.loseConnection()
		self.client = None
		
		if FRONTEND_USER_CONNS.has_key(self.auth_user):
			FRONTEND_USER_CONNS[self.auth_user] = max(0, FRONTEND_USER_CONNS[self.auth_user] - 1)
		CURRENT_BACKEND_CONNS = max(0, CURRENT_BACKEND_CONNS - 1)
		
		if self.remove_conn_on_lost == True:
			CURRENT_TEMP_CONNS = max(0, CURRENT_TEMP_CONNS - 1)
		
		if CONN_ID[self.conn_id]["AUTH"] == True and CONN_ID[self.conn_id]["USER"].startswith("READER-"):
			CURRENT_READER_CONNS = max(0, CURRENT_READER_CONNS - 1)
		
		self.traffic_data(True)
		thread = threading.Thread(name='update_traffic_db',target=self.update_traffic_db)
		thread.start()
		duration = int(time.time() - self.conn_start)
		if not self.auth_user == None:
			message  = "F_DIS: (.)(cid='%s') '%s' dur='%d' dl_bytes='%d' up_bytes='%d' dl_lines='%d' up_lines='%d'"  % (self.conn_id, self.auth_user, duration, CONN_ID[self.conn_id]["TRAF"]["RX_BYTES"], CONN_ID[self.conn_id]["TRAF"]["TX_BYTES"], CONN_ID[self.conn_id]["TRAF"]["RX_LINES"], CONN_ID[self.conn_id]["TRAF"]["TX_LINES"])
			dbg(1,message)
		del CONN_ID[self.conn_id]

	def lineReceived(self, line):
		dbg(9,"F_CON: (%d/%d)(cid='%s'): lineR = '%s'"%(CONN_ID[self.conn_id]["TRAF"]["RX_LINES"],CONN_ID[self.conn_id]["TRAF"]["TX_LINES"],self.conn_id,line))
		global LOCAL_USERS
		global USER_CONNECTIONS
		global CURRENT_BACKEND_CONNS
		global FRONTEND_USER_CONNS
		global CURRENT_TEMP_CONNS
		global CURRENT_READER_CONNS
		dbg(9,"line = '%s'" % (line))
		lined = False
		
		if line.upper().startswith('AUTHINFO USER '):
			LOCAL_USERS = CONFIG["USERS"]
			USER_CONNECTIONS = CONFIG["CONNS"]
			data = line.split(' ')
			if len(data) == 3: 
				self.auth_user = data[2].strip()
			else: 
				self.auth_user = ''
			if LOCAL_USERS.has_key(self.auth_user):
				self.sendLine('381 SEND PASS')
			else:
				lined = '481 USER UNKNOWN'
		elif line.upper().startswith('AUTHINFO PASS '):
			data = line.split(' ')
			if len(data) == 3 and LOCAL_USERS.get(self.auth_user) == hashlib.sha256(data[2].strip()).hexdigest():
				if not FRONTEND_USER_CONNS.has_key(self.auth_user): 
					FRONTEND_USER_CONNS[self.auth_user] = 1
				else: 
					FRONTEND_USER_CONNS[self.auth_user] += 1
				CURRENT_BACKEND_CONNS += 1
				CURRENT_TEMP_CONNS = max(0, CURRENT_TEMP_CONNS - 1)
				if USER_CONNECTIONS.has_key(self.auth_user):
					if CURRENT_BACKEND_CONNS <= CONFIG["BACKEND"]["conn"]:
						if FRONTEND_USER_CONNS[self.auth_user] <= USER_CONNECTIONS[self.auth_user]:
							# Frontend connection authenticated
							CONN_ID[self.conn_id]["AUTH"] = True
							CONN_ID[self.conn_id]["USER"] = self.auth_user
							dbg(1,"F_CON: (.)(cid='%s'): '%s' %d/%d BE=[%d/%d]" % (self.conn_id,self.auth_user,FRONTEND_USER_CONNS[self.auth_user],USER_CONNECTIONS[self.auth_user],CURRENT_BACKEND_CONNS,CONFIG["BACKEND"]["conn"]))
							self.client.sendLine('AUTHINFO USER %s' % CONFIG["BACKEND"]["user"])
							dbg(5,"B_CON: (.)(cid='%s'): SENT LOGIN TO BACKEND"%(self.conn_id))
							
							# pass subsequent lines to backend
							self.lineReceived = self._LineToBackend
							return
						else:
							lined = '502 LIMIT'
					else:
						lined = '502 BECONN'
				else:
					lined = '481 CONFIG FAIL'
			else:
				lined = '481 PASS FAIL'
		elif line.upper() == 'MODE READER':
			dbg(9,"mode reader requested")
			SPLITLINE = line.split(' ')
			if SPLITLINE[1] == "READER":
				#if CONFIG["BACKEND"]["mode"].upper() == "READER":
				if ALLOW_MODE_READER == True:
					if CURRENT_BACKEND_CONNS < CONFIG["BACKEND"]["conn"]:
						if CURRENT_READER_CONNS < MAX_READER_CONNS:
							CURRENT_TEMP_CONNS = max(0, CURRENT_TEMP_CONNS - 1)
							CURRENT_BACKEND_CONNS += 1
							CURRENT_READER_CONNS += 1
							self.auth_user = "READER-%d" % (CURRENT_READER_CONNS)
							CONN_ID[self.conn_id]["AUTH"] = True
							CONN_ID[self.conn_id]["USER"] = self.auth_user
							dbg(1,"F_CON: (.)(cid='%s') '%s' %d/%d BE=[%d/%d]" % (self.conn_id,self.auth_user,CURRENT_READER_CONNS,MAX_READER_CONNS,CURRENT_BACKEND_CONNS,CONFIG["BACKEND"]["conn"]))
							
							self.client.sendLine('AUTHINFO USER %s' % CONFIG["BACKEND"]["user"])
							dbg(5,"B_CON: (.)(cid='%s') SENT LOGIN TO BACKEND"%(self.conn_id))
							
							# pass subsequent lines to backend
							self.lineReceived = self._LineToBackend
							return
						else:
							lined = '502 READER LIMIT'
					else:
						lined = '502 READER BECONN'
				else:
					lined = '502 TRANSIT ONLY'
			else:
				lined = '501 INVALID MODE'
		elif line.upper() == 'HELP':
			self.sendLine('500 SOS')
			return
		elif line.upper() == 'QUIT':
			self.sendLine('205 CYA')
			return
		elif line.upper() == 'CAPABILITIES':
			self.sendLine('101 Capabilities list:')
			self.sendLine('VERSION 1')
			self.sendLine('AUTHINFO USER PASS')
			self.sendLine('.')
			return
		elif line.upper().startswith('GROUP '):
			# do not allow GROUP COMMAND without AUTH
			lined = '480 Permission denied'
		else:
			lined = '502 UNKNOWN CMD'
		
		# overwrite line to frontend
		if not lined == False:
			msg = "user %s: lined='%s' line='%s'" % (repr(self.auth_user),lined,line)
			dbg(9,msg)
			self.sendLine(lined)
			return self.transport.loseConnection()

	def _LineToBackend(self, line):
		# called everytime a frontend connection passed a line after authentication
		global USER_TRAFFIC
		global CONN_ID
		lined = False
		
		if CONN_ID[self.conn_id]["BACK"] == False:
			# silent return if conn_id has no backend conn yet
			return
		elif lineisascii(line) == False:
			dbg(9,"WARN: Frontend drop NONASCII line")
			lined = '500 LINE NOT ASCII'
		elif len(line) > 999:
			dbg(9,"WARN: Frontend drop LONG line")
			lined = '500 LINE TOOO LONG'
		
		if ALLOW_MODE_READER  == True:
			ALLOW_CMDS = ( 'ARTICLE', 'BODY', 'GROUP', 'HEAD', 'STAT', 'LIST', 'NEWGROUP', 'XOVER' )
		else:
			ALLOW_CMDS = ( 'ARTICLE', 'GROUP' )
		
		SPLITLINE = line.split(' ')
		lenSPLITLINE = len(SPLITLINE)
		F_CMD = False
		
		if not line.upper().startswith(ALLOW_CMDS):
			# if else: command from frontend is not allowed
			if line.upper().startswith('POST'):
				# reject POST command
				lined = '440 NOPOST'
			elif line.upper().startswith('IHAVE'):
				# reject IHAVE command
				lined = '437 REJECT'
			elif line.upper().startswith('QUIT'):
				# user sent QUIT, we follow!
				lined = '205 CLOSED'
			else:
				# drop any other commands
				lined = '500 UNKNOWN'
		else:
			F_CMD = SPLITLINE[0].upper()
		
		self.traffic_data()
		linelen = len(line)
		now = int(time.time())
		
		USER_TRAFFIC[self.auth_user]["TX_BYTES"] += linelen
		USER_TRAFFIC[self.auth_user]["TX_LINES"] += 1
		USER_TRAFFIC[self.auth_user]["LAST_ACT"] = now
		
		CONN_ID[self.conn_id]["TRAF"]["TX_BYTES"] += linelen
		CONN_ID[self.conn_id]["TRAF"]["TX_LINES"] += 1
		CONN_ID[self.conn_id]["LAST"] = now
		
		if not F_CMD == False and lined == False:
			dbg(9,"F_CON: (%d/%d)(cid='%s'): F_CMD '%s'"%(CONN_ID[self.conn_id]["TRAF"]["RX_LINES"],CONN_ID[self.conn_id]["TRAF"]["TX_LINES"],self.conn_id,line))
			if F_CMD == 'ARTICLE' or F_CMD == 'BODY' and lenSPLITLINE == 2:
				pass
			elif F_CMD == 'ANY CMD ':
				pass
			else:
				dbg(3,"F_CON: (%d/%d)(cid='%s'): F_CMD '%s'"%(CONN_ID[self.conn_id]["TRAF"]["RX_LINES"],CONN_ID[self.conn_id]["TRAF"]["TX_LINES"],self.conn_id,F_CMD))
				
			# pass command from frontend to backend
			dbg(9,"TX_BE: (%d/%d)(cid='%s'): '%s' "%(CONN_ID[self.conn_id]["TRAF"]["RX_LINES"],CONN_ID[self.conn_id]["TRAF"]["TX_LINES"],self.conn_id,line))
			self.client.sendLine(line)
			return
		
		if lined == False:
			# send response to frontend
			self.sendLine(line)
		else:
			# overwrite response to frontend
			self.sendLine(lined)
			self.transport.loseConnection()
			dbg(9,"def _LineToBackend: lined='%s' line='%s'"%(lined,line))
		return

	def update_traffic_db(self):
		global USER_TRAFFIC
		global UPDATE_TRAFFICDB_RUNNING
		if UPDATE_TRAFFICDB_RUNNING == True:
			return
		UPDATE_TRAFFICDB_RUNNING = True
		dbg(9,"def update_traffic_db()")
		try:
			url = "http://127.0.0.1:81/nntp"
			for key,data in USER_TRAFFIC.items():
				username = key
				dl_bytes = data["RX_BYTES"]
				up_bytes = data["TX_BYTES"]
				dl_lines = data["RX_LINES"]
				up_lines = data["TX_LINES"]
				duration = data["duration"]
				activity = data["LAST_ACT"]
				tr_total = (dl_bytes + up_bytes)
				li_total = (dl_lines + dl_lines)
				idletime = int(time.time() - activity)
				try:
					dl_speed = int(dl_bytes / duration)
				except:
					dl_speed = 0
				try:
					up_speed = int(up_bytes / duration)
				except:
					up_speed = 0
				tsend_1 = (1 * 1000 * 1000 * 1000) # 1GB
				tsend_2 = (1 * 1000 * 1000) # 1MB
				posted = False
				if tr_total > tsend_1 or duration > 7200 or idletime > 900:
					values = {'user' : username, 'duration' : duration, 'downloaded' : dl_bytes, 'uploaded' : up_bytes, 'dl_lines' : dl_lines, 'up_lines' : up_lines, 'port' : CONFIG["BACKEND"]["call"], 'dl_speed':dl_speed, 'up_speed':up_speed }
					if dl_bytes > tsend_2:
						try:
							r = requests.post(url,data=values)
							if not r.content == "200":
								raise Exception('POST FAILED')
							else:
								dbg(1,"def update_traffic_db: POSTED values = '%s'"%(values))
								posted = True
						except Exception as e:
							pass
							dbg(1,"def update_traffic_db: failed #1, exception = '%s'"%(e))
					if posted == False:
						dbg(1,"update_traffic_db: NOPOST values = '%s'"%(values))
					del USER_TRAFFIC[self.auth_user]
		except Exception as e:
			dbg(1,"def update_traffic_db: failed #2, exception = '%s'"%(e))
		UPDATE_TRAFFICDB_RUNNING = False
		return

	def traffic_data(self,lost=False):
		global USER_TRAFFIC
		try:
			dbg(9,"traffic_data: %s"%(self.auth_user)) 
			try:
				test = USER_TRAFFIC[self.auth_user]
			except:
				USER_TRAFFIC[self.auth_user] = {}
				USER_TRAFFIC[self.auth_user]["duration"] = 0
				USER_TRAFFIC[self.auth_user]["RX_BYTES"] = 0
				USER_TRAFFIC[self.auth_user]["TX_BYTES"] = 0
				USER_TRAFFIC[self.auth_user]["RX_LINES"] = 0
				USER_TRAFFIC[self.auth_user]["TX_LINES"] = 0
				USER_TRAFFIC[self.auth_user]["LAST_ACT"] = 0
				dbg(9,"def traffic_data: create USER_TRAFFIC[%s]"%(self.auth_user))
			
			if lost == True:
				USER_TRAFFIC[self.auth_user]["duration"] += int(time.time() - self.conn_start)
			
			dbg(9,"USER_TRAFFIC[%s] = '%s'"%(self.auth_user,USER_TRAFFIC[self.auth_user]))
		except Exception as e:
			dbg(1,"def traffic_data: failed, exception = '%s'"%(e))

class Backend(LineReceiver):
	server = None

	def connectionMade(self):
		dbg(5,"Backend connectionMade")
		self.server.client = self
		self.server.transport.resumeProducing()

	def connectionLost(self, reason):
		dbg(5,"Backend connectionLost:" % reason)
		if self.server is not None:
			self.server.transport.loseConnection()
			self.server = None

	def connectionFailed(self, reason):
		dbg(5,"Backend connectionFailed:" % reason)
		if self.server is not None:
			self.server.transport.loseConnection()
			self.server = None

	def lineReceived(self, line):
		# called everytime we received a line from backend
		global USER_TRAFFIC
		global CONN_ID
		
		auth_user = self.server.auth_user
		conn_id = self.server.conn_id
		
		self.server.traffic_data()
		linelen = len(line)
		now = int(time.time())
		rx_info = "RX_BE: (%d)(cid='%s'):" % (CONN_ID[conn_id]["TRAF"]["RX_LINES"],conn_id)
		
		USER_TRAFFIC[auth_user]["RX_BYTES"] += linelen
		USER_TRAFFIC[auth_user]["RX_LINES"] += 1
		USER_TRAFFIC[auth_user]["LAST_ACT"] = now
		CONN_ID[conn_id]["TRAF"]["RX_BYTES"] += linelen
		CONN_ID[conn_id]["TRAF"]["RX_LINES"] += 1
		CONN_ID[conn_id]["LAST"] = now
		
		if CONN_ID[conn_id]["BACK"] == True:
			response = 0
			if lineisascii(line) == True:
				# enable this dbg to see received ASCII lines from backend
				dbg(10,"%s ASCII: '%s' "%(rx_info,line))
				
				SPLITLINE = line.split(' ')
				if len(SPLITLINE) > 1:
					# check backend response codes
					try:
						response = int(SPLITLINE[0])
					except:
						dbg(9,"pass line without response code")
						pass
			
			if response > 0:
				# enable this dbg to see ALL response codes from backend
				dbg(6,"%s '%s' "%(rx_info,line))
		
		# some talkink with backend
		if CONN_ID[conn_id]["BACK"] == False:
			if line.startswith('200 ') or line.startswith('201 '):
				self.server.sendLine('201 NNTP')
				dbg(5,"B_CON: (.)(cid='%s') '%s'"%(conn_id,line))
				return
			elif line.startswith('480 '):
				# backend requests USER
				self.sendLine('AUTHINFO USER %s' % CONFIG["BACKEND"]["user"])
				dbg(5,"B_CON: (.)(cid='%s') 480, sent: 'AUTHINFO USER'"%(conn_id))
				return
			elif line.startswith('381 '):
				# backend requests PASS
				self.sendLine('AUTHINFO PASS %s' % CONFIG["BACKEND"]["pass"])
				dbg(5,"B_CON: (.)(cid='%s') AUTHINFO"%(conn_id))
				return
			elif line.startswith('281 '):
				# BACKEND AUTHENTICATED
				self.server.sendLine('281 OK')
				dbg(5,"B_CON: (.)(cid='%s') 281 OK"%(conn_id))
				CONN_ID[conn_id]["BACK"] = True
				return
			elif line.startswith('40 ') or line.startswith('48 ') or line.startswith('50 '):
				lined = "502 Backend Error"
				dbg(1,"B_ERR: (.)(cid='%s') lined='%s' line='%s'"%(conn_id,lined,line))
				self.server.sendLine(lined)
				self.server.transport.loseConnection()
				self.transport.loseConnection()
				CONFIG["BACKEND"]["OFFLINE"] = True
				return
		else:
			self.server.sendLine(line)

class BackendFactory(ClientFactory):
	server = None
	protocol = Backend

	def buildProtocol(self, *args, **kw):
		dbg(5,"BackendFactory: def buildProtocol")
		prot = ClientFactory.buildProtocol(self, *args, **kw)
		prot.server = self.server
		return prot

	def clientConnectionLost(self, connector, reason):
		dbg(5,"BackendFactory: Connection Lost")
		self.server.transport.loseConnection()

	def clientConnectionFailed(self, connector, reason):
		dbg(5,"BackendFactory: Connection Failed")
		self.server.transport.loseConnection()

IDLE_TIMER()
while CONFIG == False:
	print("loading config")
	time.sleep(1)

if CONFIG["BACKEND"]["logs"] == True:
	LOGFILE = CONFIG["BACKEND"]["logf"]
	if os.path.isfile(LOGFILE):
		try:
			os.remove(LOGFILE)
		except:
			print("logfile remove failed: %s"%(LOGFILE))
			sys.exit(1)
	log.startLogging(file(LOGFILE, 'a'))

FrontendFactory = ServerFactory()
FrontendFactory.protocol = Frontend
FrontendFactory.protocol.clientFactory = BackendFactory
reactor.listenTCP(CONFIG["LPORT"], FrontendFactory)
reactor.run()
