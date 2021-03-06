import serial
import serial.tools.list_ports
import time
import threading
import RepeatTimer
import sys
import math
import inspect


import channels
import logger


DEFAULTOUTFILE = "test.txt"
MAX_EID = 200
MSG_HEAD = 2
VERNUM = 10		# version 1.0
EOP = "|"
ESC = "`"


#keyTable = {0:"talk",1:"over",2:"bad",3:"version",4:"start",5:"stop",6:"set",7:"dir",8:"query",9:"info",10:"dig",11:"wav"} 
keyTable = ["talk","over","bad","version","start","stop","set","dir","query","info","dig","wav","point","sync","avg", "timer", "event", "resetevents", "trigger"]


# -- Class that holds one integer point.
class Data():
	time = 0	# absolute time this data was taken
	systime = 0	# system time this data point was recieved
	clk = 0		# prop clock time this data point was taken
	value = 0	# value of this data point
	def __init__(self,rtime, clk, value):
		self.systime = time.time() # ticks since epoch
		self.time = rtime
		self.clk = clk
		self.value = value
	
	def __str__(self):
		return str(self.systime) + "," + str(self.time) + "," + str(self.clk) + "," + str(self.value) + "\n"

# class Device Class to represent the device as a whole. includes  a PropCom object for direct communication.
# The Device class is useful for dealing with Channels instead of raw communication packets.

class Device():

	actions = ["SetTimer", "Notify", "AIStart", "AIStop", "DOHigh", "DOLow"] 
	conditions = ["TimerExpire", "Always", "OnChange", "OnHigh", "OnLow", "WhileHigh", "WhileLow", "OnTrigger"]

	propCom = None
	analogIn = dict()
	analogOut = dict()
	digitals = None
	# constructor Device( Int nAnalogI, Int nAnalogO, Int nDigitals ) return Device 
	# nAnalogI = number of analog input channels on the device
	# nAnalogO = number of analog output channels for the device
	# nDigitals = number of digital channels. includes digital inputs and digital outputs.

	def __init__(self, nAnalogI, nAnalogO, nDigitals):
		self.propCom = PropCom()
		self.analogIn = dict()
		self.analogOut = dict()
		self.digitals = None
		self.channels = dict()
		self.digitalIdx = nAnalogI + nAnalogO

		for idx in range(nAnalogI):
			name = "Analog Input " + str(idx)
			cIdx = idx
			self.analogIn[cIdx] = channels.AnalogIn( self.propCom, cIdx, name=name ) 
			self.channels[cIdx] = self.analogIn[cIdx]
		for idx in range(nAnalogO):
			name = "Analog Output " + str(idx)
			cIdx = idx + nAnalogI
			self.analogOut[cIdx] = channels.AnalogOut( self.propCom, cIdx, name=name) 
			self.channels[cIdx] = self.analogOut[cIdx]

		name = "Digital I/O"
		cIdx = nAnalogI + nAnalogO
		self.digitals = channels.Digitals( self.propCom, cIdx, nDigitals, name=name)  
		self.channels[cIdx] = self.digitals


	# function Device.setNAvg(Int nAvg) set the number of samples to average on the device. Any sample will be an average of nAvg samples.
	def setNAvg(self, nAvg):
		if nAvg < 1:
			nAvg = 1
		self.propCom.nAvg = int(nAvg)

		for x in self.analogIn:
			idx = self.channels[x].idx
			if self.channels[x].started and self.channels[x].value/self.propCom.nAvg<self.propCom.MIN_ADC_PERIOD and self.propCom.nAvg != 1:
				self.setNAvg(self.channels[x].value/self.propCom.MIN_ADC_PERIOD)
				# notify user of change
				logger.message("Average filter is too high. \n Setting to " + str(self.propCom.nAvg) + " sample average.")
		if self.propCom.nAvg > self.propCom.MAX_AVG:
			self.propCom.nAvg = self.propCom.MAX_AVG
			logger.message("Average filter is too high. \n Setting to " + str(self.propCom.nAvg) + " sample average.")

		self.propCom.send("avg", self.propCom.nAvg)
		
	# function Device.queryChannel( Int chan ) Query the specified channel number for its state information like sample rate, start/stop state, etc.
	# chan = The channel number of the channel to querry. Leave blank to querry all channels
	def queryChannel(self, chan=None):
		if chan is None:
			for x in self.analogIn:
				idx = self.channels[x].idx
				self.propCom.send("query", idx)
			for x in self.analogOut:
				idx = self.channels[x].idx
				self.propCom.send("query", idx)
			idx = self.digitals.idx
			self.propCom.send("query", idx)
		else:
			if chan in self.channels:
				idx = self.channels[chan].idx
				self.propCom.send("query", idx)
			else:
				logger.log("Bad Channel Querry", chan, logger.WARNING)
	# function Device.addEvent( String condition, Int condParam, String action, Int actionParam ) Add a new event, that executes *action* with specified parameters in *actionParam* when *condition* is met with the given parameters in *condParam*. *condition* and *action* should match entries in the device condition and action tables.
	def addEvent(self, condition, condParam, *actionArg):

		actions = [0] * len(actionArg)
		for n in range(0,len(actionArg),2):
				actions[n] = self.actions.index(actionArg[n])
				actions[n+1] = actionArg[n+1]

		self.propCom.send( "event", [self.conditions.index(condition), condParam] + actions )

	#function Device.setEventTimer(Int timerID, Int time) Sets the timer specified by *timerID* to match the time, in device clock cycles, given by *time*
	def setEventTimer(self, timerID, time):
		self.propCom.send( "timer", [timerID, time])
	#function Device.eventTrigger(Int trigger) Activates an external trigger for OnTrigger events
	def eventTrigger(self, trigger):
		self.propCom.send( "trigger", trigger )
	#function Device.resetEvents() Reset and remove all events from the event loop.
	def resetEvents(self):
		self.propCom.send("resetevents")





# class PropCom The communication object. All communication to the device is done through this class.
# This class has methods to handle incoming messages, send control message. It does not know about channel information.
# All methods deal with raw communication packets. Use Channel or Device objects for more abstraction.
# PropCom is a Thread object. The PropCom should be in its "open" state before starting the thread. Use *open* method.
class PropCom(threading.Thread):
	CLOCKPERSEC = 80000000
	SYNCPERIOD = 80000000
	CLOCKERROR = 20000
	MIN_ADC_PERIOD = 1500
	MAX_AVG = 100
	MAX_CLOCK = (1<<32) - 1
	MAX_RATE = 1500 # max sampling rate in samples per second
	nAvg = 1
	name = "?"
	com = None
	comOpen = False
	comlock = threading.Lock()
	ID = 0		# fixme (used to make a new ID for repeating timers) better way than counter? uuid? TODO
	msgID = 20
	lastMsgID = 0
	locks = dict() # dictionary of lock ID's -> timer objects (to keep track of active timers)
	eIDs = dict()

	# dictionary of callback functions for each 
	callbacks = dict()
	echoCallbacks = dict()


	# constructor PropCom( Dict callbacks ) Generates a new PropCom object in an idle state. It will not be useful until its *start* method is called.
	# callbacks = If specified, the new PrpCom object will start with its callback table initialized to this dictionary.
	def __init__ (self, callbacks=None):
		if callbacks is not None: self.callbacks = callbacks
		self.com = serial.Serial(timeout=None)
		threading.Thread.__init__(self)
		self.daemon = True
		self.setDaemon(True)
		self.lastPkt = 0
		self.lastTStamp = dict()
		self.lastRTime = dict()
		self.lastTime = None
		#self.lastRTime = 0
		self.cnt = 0
		self.MAXTICK = (1 << 32) -1
		self.port=None # initial port to attempt to open. overrides default search
		self.listeners = [set(),set(),set(),set(),set(),set(),set(),set()] # length 8 list of sets

	# function PropCom.run() Starts a new thread to read information from the com buffers.
	# The PropCom must first be in the open state before this method is called. 
	# A new thread is created that will terminate when the connection is closed.
	# This method should not be called directly.
	def run(self):
		self.open(self.port)
		buf = EOP + " "
		waiting = 0
	
		while self.isOpen():
			# try to read in new info
			try:
				c = self.com.read(1)
				buf += c
				if c == EOP:
					c = self.com.read(1)
					buf += c
					prebuf = buf
					buf = self.parse(buf)
					if logger.options["log_buffer"] and buf != prebuf:
						logger.write(prebuf + " Parsed to " + buf)
			except serial.SerialException as err:
				logger.log("SerialException on read", err,logger.WARNING)
				self.close() # clean-up
				break
	# function PropCom.restart() Restarts the objects thread by making a new PropCom object with the same callbacks table.
	def restart(self):
		self.close()
		thread.sleep(1000)
		newSelf = PropCom(callbacks=self.callbacks)
		newSelf.start()
		return newSelf
	
	# function PropCom.onSync( Int tStamp ) Sync the PropCom objects internal clock state to reflect the device's CPU clock.
	# The PropCom object keeps track of timestamps and can change from timestamps to system time.
	# Should be called on every *sync* packet. 
	def onSync(self, tStamp):
		if self.lastTime is None:
			self.firstTime = tStamp
			self.lastTime = tStamp
			if logger.options["log_sync"]:
				logger.write( "first: "  + str(self.lastTime) )
			return 
			
		#updates the clock counter with a new value. tests for overflow.
		if tStamp >= self.lastTime:
			elapsedTicks = tStamp - self.lastTime
			if logger.options["log_sync"]:
				logger.log( "sync", str(self.lastTime) + " -> " + str(tStamp), logger.INFO)
		else:
			elapsedTicks = tStamp + (self.MAXTICK - self.lastTime)
			if logger.options["log_sync"]:
				logger.log( "sync (rollover)", str(self.lastTime) + " -> " + str(tStamp), logger.INFO)

		if logger.options["log_sync"]:
			if elapsedTicks < self.SYNCPERIOD - self.CLOCKERROR:
				logger.log( "sync too soon! not enough ticks!", elapsedTicks, logger.WARNING)
			if elapsedTicks > self.SYNCPERIOD + self.CLOCKERROR:
				logger.write( "sync too late! too many ticks!", elapsedTicks, logger.WARNING)

		self.cnt += elapsedTicks
		self.lastTime = tStamp
		if logger.options["log_sync"]:
			logger.write( str(self.cnt) + "ticks. " + str(self.curTime()) + "seconds from first sync. estimated " + str(self.estTime()))
		if abs(self.curTime() - self.estTime()) > 1.0: #Adjust if time has strayed
			logger.log("Significant Timing difference between curTime() and estTime()" , str(self.curTime() - self.estTime()),logger.ERROR)
			self.cnt -= (self.curTime() - self.estTime())*self.CLOCKPERSEC

	# function PropCom.curTime() return Float the current time in seconds since the first sync.
	def curTime(self):
		return (self.cnt) / float(self.CLOCKPERSEC)
	# function PropCom.estTime() return Float an estimated time in seconds since the first sync. Uses system clock and is imprecise.
	def estTime(self):
		return time.time()-self.firstSyncTime

	# function PropCom.realTime(Int tStamp) return Float The time in seconds since the first sync this timestamp corresponds to.
	# tStamp = the timestamp to be converted. Must be within +- 1/2 clock cycle since the last sync to avoid errors
	def realTime(self, tStamp, streamID=-1):
		if tStamp >= self.lastTime and tStamp - self.lastTime < self.MAXTICK/2: 			# this timestamp is after last sync, and no rollovers
			elapsedTicks = tStamp - self.lastTime
		elif self.lastTime - tStamp > self.MAXTICK/2: 	# this timestamp is new, but rolled over since last sync
			#logger.write( "!!" + str(self.lastTime) + "->" + str(tStamp))
			elapsedTicks = tStamp + (self.MAXTICK - self.lastTime)
		elif tStamp < self.lastTime:  						# this timestamp is slightly old, assuming it is not garbage data.
			elapsedTicks = tStamp - self.lastTime #returns a negative elapsed time.
		elif tStamp > self.lastTime and tStamp - self.lastTime > self.MAXTICK/2: #this timestamp is in the past, but clock recently rolled over.
			logger.write("!!@" + str(self.lastTime) + "->" + str(tStamp))
			elapsedTicks = tStamp - self.MAXTICK - self.lastTime
		else:
			logger.log("No condition matched for 'realTime()'","Propellor.py", logger.ERROR)
			elapsedTicks = 0

		try:
			lastRTime = self.lastRTime[streamID]
			lastTStamp = self.lastTStamp[streamID]
		except KeyError as e:
			lastRTime = 0
			lastTStamp = 0

		rTime = (self.cnt + elapsedTicks) / float(self.CLOCKPERSEC)
		if lastRTime > rTime + 0.5:
			logger.log("Went back in time??? [" + str(streamID) + "]  ("+str(lastTStamp)+"->"+str(tStamp)+") Dif="+str(lastTStamp-tStamp)+"ticks, " + str(lastRTime-rTime)+"seconds",rTime,logger.WARNING)
		self.lastTStamp[streamID] = tStamp
		self.lastRTime[streamID] = rTime


		return rTime
	# functino PropCom.nextMsgID() return Int a sequential message ID for the next message to be sent.
	def nextMsgID(self):
		self.msgID = (self.msgID + 1) & 255
		if self.msgID == 0:
			self.msgID = (self.msgID + 1) & 255
		return self.msgID
	# function PropCom.newID() return Int a new unique ID.
	def newID(self):
		self.ID = (self.ID + 1)
		return self.ID

	# function PropCom.addListener( Int streamID, StreamListener obj ) Appends the SteamListener object to a list of objects listening to the given stream.
	# Events that can be used are ...
	# streamID = The ID of the stream of interest
	# obj = StreamListener object that has methods to react to any events of interest. 
	def addListener(self, streamID, obj):
		self.listeners[streamID].add(obj)
	
	# function PropCom.removeListener( Int streamID, StreamListener obj ) Removes the StreamListener from the list of objects listening to this stream.
	# If no such object is registered, a KeyError is raised.
	# steamID = the ID of the stream of interest
	# obj = StreamListener object ot be removed from the list.
	def removeListener(self, streamID, obj):
		try:
			self.listeners[streamID].remove(obj)
		except KeyError as E:
			logger.log("No function registered in channel " + str(self.idx),  str(obj), logger.WARNING)
			raise


	# function register( String name, Function func, Function test)
	# Registers the given function to the given message name. func will be called for any packet with of the given message type.
	# func = Called any time a matching control packet is recieved.
	# test = A predicate can be used to only execute func if test returns true.
	def register(self, name, func, test=None):
		ID = self.newID()
		if name not in self.callbacks:
			self.callbacks[name] = dict()
		self.callbacks[name][ID]=(test, func)
		return ID
	# function deregister( String name, Int funcID ) return Bool|None true if successful. If no such function is registered, None is returned and a KeyError is raised.
	# name = Name of the message type the functino is registered to
	# funcID = the function ID returned by the register function of the function to deregister.
	def deregister(self, name, funcID):
		try:
			rval = self.callbacks[name][funcID]
			del (self.callbacks[name])[funcID]
		except KeyError as E:
			rval = None
			logger.log("No function registered", name + ":" + str(funcID), logger.WARNING)
			raise

		return rval
	# open COM port for this prop. used to find prop waiting on ports
	# function PropCom.open( String port ) Opens tyhe specified serial port for reading and writing. If no port is specified, the first available port that responds is opened.
	# port = A string representation of the port to open. on windows it might look like "COM3"

	def open(self, port=None):
		self.com.baudrate = logger.options["baud"]
		def openPort(self, port):
			try:
				self.com.port = port
				self.com.open()
				logger.log("Opened port",self.com.port,logger.INFO)
				self.comOpen = True
				self.firstSyncTime = time.time() #sync packets should start accumulating as soon as we open the port
			except Exception as e:
				self.comOpen = False
				logger.log("openPort Failed",e,logger.ERROR)
			time.sleep(3)
			self.send("version") # start the dialog
			return

		if port is None:
			self.openFirstProp(openPort) # opens the first com port
		else:
			openPort(self,port)
	# function PropCom.isOpen() return Bool True if a serial port is open, False otherwise.
	def isOpen(self):
		return self.comOpen
	# function PropCom.close() Close the currently active serial port and stop any channels
	def close(self):
		comOpen = False
		self.send("stop",0) # stops all channels.
		self.com.close()
		# kill locks. 
		for idx,t in self.locks.iteritems():
			t.cancel()
			del self.locks[idx]


	# function PropCom.send( String|Int key, String value) return Int 1 if successful. -1 on most errors.
	# Send a control packet to the device using the currently active serial port.
	# key = A byte representing the message type. if a String is passed, a dictionary is used to convert into an int. All message types are listed in the firmware wiki section.
	def send(self, key, value=None ):
		""" sends a control packet with a message ID that corresponds to the string value 'key', with parameters specified in value.
			key is a string that represents the message ID, or and int specifing the message ID.
			value is either an integer, or a list of integers."""
		

		if self.com is None or self.isOpen() == False:
			logger.log("send on bad port", key, logger.WARNING)
			return -1
	
		if key is None:
			logger.log("send NoneType key", key, logger.WARNING)
			return -1

		try:
			msg = chr(key) + chr(self.nextMsgID())
		except TypeError: # key is not an int. treat as string.			
			if key not in keyTable and key :
				logger.log("Attempting invalid control msg ID", key, logger.WARNING)
				return -1
			msg = chr(keyTable.index(key)) + chr(self.nextMsgID())

		if value is not None:
			try:
				for v in value:
					for n in range(4):
						msg += chr( (v>>24-n*8)&255 )
			except TypeError: # value is not a list. treat as int.
				for n in range(4):
					msg += chr( (int(value)>>24-n*8)&255 )
		msg = msg.replace(ESC, ESC+ESC)
		msg = msg.replace(EOP, ESC+EOP)
		chksum = 0
		for c in msg:
			chksum = ((chksum<<1) | (chksum>>7)) & 255 # left-rotate
			chksum = (chksum + ord(c)) % 256           # 8-bit addition
		msg = msg + EOP + chr(chksum)
		if logger.options["log_sent"]:
			logger.log( "sending ", str(key) + " " + str(value), logger.INFO)
			logger.log( "	raw: ", msg.replace("\a","@"), logger.INFO)
		self.comlock.acquire(True)	#block until lock taken	
		try:
			retv = self.com.write(msg)
		except (serial.serialutil.portNotOpenError, ValueError, serial.serialutil.SerialTimeoutException) as err:
			logger.log("Writing to closed port", err, logger.WARNING)
			return -1
		except serial.SerialException as err:
			logger.log("SerialException on write", err, logger.WARNING)
			return -1
		self.comlock.release()
		return 1 

		# parse all the keys in "resp". 
	# function PropCom.parse ( String ) return String any unused characters leftover after parsing all packets.
	# Parse the given String for any packets. For any control packets, parseControl is called, for stream packets, parseStream is called.
	def parse(self, msgBuffer ):
	  global DBG1
 	  DBG1=""
	  #find last EOP
	  n = 0
	  end = 0
	  
	  while not end == -1:
	    n = 0
	    escaped = 0
	    chksum = 0
	    chk = 0
	    state = 0
	    packet = ""
	    end = -1
	    
	    while end == -1:
	      if n == len(msgBuffer):
		end = -1
		DBG1+="X"
		break
	      c = msgBuffer[n]
	      n+=1
	      if state == 0: # finding first EOP
		if not escaped and c == EOP:
		  state = 1
		  DBG1+="{"
		else:
		  DBG1+="-"
	      elif state == 1: # checksum of last packet. useless.
		DBG1+="$("+str(ord(c))+")"
		chksum=0
		state = 2
	      elif state == 2: # collect packet data
		if not escaped and c == EOP:
		  DBG1+="}"
		  state = 3 #collect checksum
		elif escaped or (not c==ESC and not c==EOP):
		  packet+=c
		  DBG1+=c.replace("\a","@")
		  DBG1+="("+str(ord(c))+")"
		  DBG1+="."
		  chksum = ((chksum<<1) | (chksum>>7)) & 255 # left-rotate
		  chksum = (chksum + ord(c)) & 255           # 8-bit addition
	         
	        if c==ESC and not escaped:
		  DBG1+="/"
	  	  escaped = 1
		  chksum = ((chksum<<1) | (chksum>>7)) & 255 # left-rotate
		  chksum = (chksum + ord(c)) &255            # 8-bit addition
	        else:
		  escaped = 0 
	      elif state == 3: # storing checksum
		chk = ord(c)
		state = 2
		DBG1+="#"
		DBG1+="("+str(ord(c))+")"
		end = n
	    if logger.options["log_parsing"]:
		    logger.write("parsed:[[" + DBG1.replace("\n","@").replace("\r","@") + "]]")
		
	    if end==-1:       
		    pass # no packets found.
	    else:
	      msgBuffer = msgBuffer[n-2:]
	      
	      if len(packet) < 1:         
	        logger.log( "Bad Packet","No bytes!", logger.WARNING)
	      elif  chk != chksum and chk!=0 and not logger.options["ignore_checksum"]:
		      if logger.options["log_bad_checksum"]:
			logger.write( "BAD CHECKSUM!")
			if ord(packet[0]) & 128:
				logger.write("BAD CHECKSUM! (stream)")
			else:
				logger.write("BAD CHECKSUM! (control)")
			logger.write( "sent:"+str(chk)+" calculated:"+str(chksum))
			if logger.options["debug_checksum"]:
				logger.write(DBG1)
	      else:
	        if ord(packet[0]) & 128:
	          self.parseStream(packet)
	        else:
		  if logger.options["log_msg"]:
	            logger.log("found",packet.replace("\a","@"),logger.INFO)
	          self.parseControl(packet)
	    
	  return msgBuffer
	  
  	# function PropCom.parseStream( string ) parses a single stream packet, and notifies any listeners registered to it.
	def parseStream(self, packet):
		''' parses a stream packet and passes parsed values to any registered stream listener objects'''
		c = ord(packet[0])
		streamID = (ord(packet[0])>>4) & 7
		if logger.options["log_stream"]:
			logger.log("Stream ["+str(streamID)+"]","",logger.INFO)

		values = []
		val = 0
		valBits=32 # bits left to read for the current val
		byteBits=4 # bits left in the current byte
		packet = chr(ord(packet[0])&15) + packet[1:]
		bytesLeft = len(packet) # bytes left in packet

		n=0

		for c in packet:
			c=ord(c)
			while byteBits>0:
				if valBits >= byteBits: # read in byteBits amount of bits
					val = (val<<byteBits) | c
					valBits -= byteBits
					byteBits = 0
				else: 			# read in valBits amount of bits, remaining bits left in c.
					val = (val<<valBits) | (c>>(byteBits-valBits))
					byteBits -= valBits
					c = c & (~(255<<valBits))
					valBits = 0
				if valBits <= 0:
					values.append(val)
					n+=1
					if logger.options["log_stream"]:
						logger.write( "   :" + str(val),True)
					val = 0
					if n<=1:
						valBits = 32
					elif bytesLeft <= 5:
						valBits = 32
					else:
						valBits = 12
			byteBits = 8 # prepare for next byte
			bytesLeft -= 1
		self.callStream(streamID, values)

	  
	# function PropCom.parseControl( String ) Parse a single control packet and call any registered functions associated with the packets message type.
	# If the packet contains any data aside from the message type, it is divided into 4byte Ints and sent as parameters to registered functions.
	def parseControl(self, packet):
	  '''parses a control packet and calls any registered hooks for the packet's message ID type.'''
	  curVal = 0 
	  state = 0
	  nameNum = -1
	  n = 0
	  m = 0
	  exData = []
	 
	  while not n == len(packet):  
	    
	    c = packet[n]
	    n+=1      
	    if state == 0: #first byte: the message ID#
		nameNum = ord(c)
		state = 1
		
	    elif state == 1: #second byte: packet #
		self.lastPkt = ord(c)  
		state = 2
	    elif state == 2: #collect packet data
		curVal = curVal << 8
		curVal += ord(c)
		m += 1
		if m==4:
		  exData.append(curVal)
		  curVal = 0
		  m = 0
          if nameNum != 13 and logger.options["log_control"]: # ignore point messages in coltrol log
	    logger.write("::" + str(nameNum) + "-" + str(self.lastPkt) + " = ",True)
	    for v in exData:
	      logger.write(v,True)
	    logger.write(" ")
	  self.call(nameNum, exData)
	  return  




  	# function PropCom.call( Int nameNum, List val ) Calls any functions associated with the given message type ID with each element in val passed as parameters.
	# nameNum = the message type ID of the packet
	# val = list of 4byte words found in the control packet.
	def call(self, nameNum, val=None):
		global DBG1
		if nameNum<len(keyTable):
			name = keyTable[nameNum]
			if name in self.callbacks:
				for key, func in self.callbacks[name].items():
					try:
						if val is None or len(val)==0:
							if func[0] is None or func[0](self): #test validator
								func[1](self)
						elif func[0] is None or func[0](self, *val): #test validator
							func[1](self, *val)
					except Exception as e:
						dbugkey = name
						logger.log( "failed call -{ " + str(dbugkey) + " }- " , str(e), logger.INFO)
						logger.log( " debug",DBG1,logger.INFO)
		else:
			logger.log("bad control ID", nameNum, logger.WARNING)
	# function PropCom.callStream(Int streamID, List values) Notifies any StreamListener objects about the incoming data. 
	# StreamListener calls are given a reference to this PropCom object.
	# streamID = The ID of the stream
	# values = new values recieved from the stream
	def callStream(self, streamID, values):
		if streamID>8:
			logger.log("Bad stream. StreamID too high!?", streamID, logger.ERROR)
			raise Exception("StreamID Too High!")

		for f in self.listeners[streamID].copy():
			try:
				f(self, values)
			except Exception as e:
				logger.log( "failed call -{ " + "stream[" + str(streamID) + "] }- ", e, logger.INFO)

	# PropCom.openFirstProp( Function openFunc ) Open the first serial port that responds to a version request.
	# When a serial port responds to a version control packet, the given function *openFunc* is called.
	# openFunc can be used to open the port.
	# openFirstProp opens all available ports and sends a version request control packet. After a small wait time specified by the global variable *DEFAULTTIMEOUT* the port is closed again.
	# after the port is closed, any response is parsed using PropCom.parse and if a valid response was recieved, openFunc is called.
	# openFunc = A function that takes 2 parameters, a PropCom object and a String representing the port
	def openFirstProp(self, openFunc):
		# store old version callback.
		if "version" in self.callbacks:
			oldVerCallback = self.callbacks["version"]
		else:	
			oldVerCallback = None
		del self.callbacks["version"]
		opened = [False]	# whether or not a valid port has been opened. 
		retry = True		# whether or not function will retry opening
		com = serial.Serial()
		com.baudrate = self.com.baudrate


		while opened[0] == False and retry == True:
			# cycle through all available ports sending <version>, and waiting for response.
			ports = serial.tools.list_ports.comports()
			for p in ports:
				logger.log("Testing port",p[0], logger.INFO)
				com.port = p[0]
				ID = 0
				try:
					com.open()

					def verHandler(propCom,  ver):
						logger.log("Response on port",com.port,logger.INFO)
						if opened[0] == False:
							opened[0] = True
							com.close()	#make sure com is closed first
							openFunc(propCom, com.port)	#open new port
						return 0
					ID = self.register("version",verHandler)
				
					verStr = chr(3) + chr(1) + EOP + chr(7)
					self.comlock.acquire(True)	#block until lock taken	
					com.write(verStr)
					self.comlock.release()		#release comlock for others!	

					time.sleep(logger.options["timeout"])
					resp = com.read(com.inWaiting())
					if logger.options["log_parsing"]:
						logger.write(resp)
					parsed = self.parse(EOP + " " + resp)
				except (serial.serialutil.SerialException, ValueError, serial.serialutil.SerialTimeoutException) as err: 
					logger.log("Error with port", com.port, logger.WARNING)
					com.close()
				finally:
					com.close() # make sure to close com port. 
					try:	# catch exceptions from invalid ID
						self.deregister("version",ID)
					except KeyError:
						logger.log("Can't Deregister version handler","ID does not exist", logger.WARNING)
					time.sleep(0.1)
			if opened[0] == False:
				retry = logger.ask("No Propeller detected. retry?", logger.QUESTION)
		
		if oldVerCallback is not None:
			self.callbacks["version"] = oldVerCallback
		else:
			del self.callbacks["version"] 
