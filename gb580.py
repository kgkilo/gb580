'''
gb580.py

Retrieve tracking data from a Globalsat GS-850(B or P) and convert it
to Garmin TCX format

copyright (C) 2012, Pablo Martin Medrano <pablo.martin@acm.org>

Redistribute or modify under the terms of the GPLv3. See
<http://www.gnu.org/licenses/>

Most of it is based on for another Globalsat model, GH-615, written
originally by speigei@gmail.com. See http://code.google.com/p/gh615/

'''

import serial, datetime, time, optparse
from pytz import timezone, utc


class Utilities():
    @classmethod
    def dec2hex(self, n, pad = False):
        hex = "%X" % int(n)
        if pad:
            hex = hex.rjust(pad, '0')[:pad]
        return hex

    @classmethod
    def hex2dec(self, s):
        return int(s, 16)

    @classmethod
    def hex2chr(self, hex):
        out = ''
        for i in range(0, len(hex), 2):
            out += chr(self.hex2dec(hex[i : i+2]))
        return out

    @classmethod
    def chr2hex(self, chr):
        out = ''
        for i in range(0, len(chr)):
            out += '%(#)02X' % {"#": ord(chr[i])}
        return out

    @classmethod
    def coord2hex(self, coord):
        '''takes care of negative coordinates'''
        coord = Decimal(str(coord))

        if coord < 0:
            return self.dec2hex((coord * Decimal(1000000) + Decimal(4294967295)),8)
        else:
            return self.dec2hex(coord * Decimal(1000000),8)

    @classmethod
    def hex2coord(self, hex):
        '''takes care of negative coordinates'''
        if hex[0:1] == 'F':
            return Decimal(self.hex2dec(hex)/Decimal(1000000)) - Decimal('4294.967295')
        else:
            return Decimal(self.hex2dec(hex)/Decimal(1000000))

    @classmethod
    def chop(self, s, chunk):
        return [s[i * chunk : (i+1) * chunk] for i in range((len(s) + chunk - 1) / chunk)]

    @classmethod
    def checkersum(self, hex):
        checksum = 0

        for i in range(0, len(hex), 2):
            checksum = checksum ^ int(hex[i:i+2], 16)
        return self.dec2hex(checksum)

    @classmethod
    def getAppPrefix(self, *args):
        ''' Return the location the app is running from'''
        isFrozen = False
        try:
            isFrozen = sys.frozen
        except AttributeError:
            pass
        if isFrozen:
            appPrefix = os.path.split(sys.executable)[0]
        else:
            appPrefix = os.path.split(os.path.abspath(sys.argv[0]))[0]
        if args:
            appPrefix = os.path.join(appPrefix,*args)
        return appPrefix


# Commands taken from gh615 code
COMMANDS = {
    'getTracklist'                    : '0200017879',
    #'setTracks'                       : '02%(payload)s%(isFirst)s%(trackInfo)s%(from)s%(to)s%(trackpoints)s%(checksum)s',
    'getTracks'                       : '0200%(payload)s%(numberOfTracks)s%(trackIds)s%(checksum)s',
    'requestNextTrackSegment'         : '0200018180',
    'requestErrornousTrackSegment'    : '0200018283',
    'formatTracks'                    : '0200037900641E',
    'getWaypoints'                    : '0200017776',
    'setWaypoints'                    : '02%(payload)s76%(numberOfWaypoints)s%(waypoints)s%(checksum)s',
    'formatWaypoints'                 : '02000375006412',
    'unitInformation'                 : '0200018584',
    'whoAmI'                          : '020001BFBE',
    'unknown'                         : '0200018382'
}


def writeserial(command, *args, **kwargs):
    hex = COMMANDS[command] % kwargs
    print 'writing to serialport ' + hex
    serial.write(Utilities.hex2chr(hex))
    # time.sleep(2)
    print 'waiting at serialport: %i' % serial.inWaiting()

def readserial(size = 2070):
    data = Utilities.chr2hex(serial.read(size))
    print 'serial port returned: %s' % data if len(data) < 30 else '%s... (truncated)' % data[:30]
    return data

def getmodel():
    writeserial('whoAmI')
    response = readserial()
    watch = Utilities.hex2chr(response[6:-4])
    print 'watch ' + watch
    product, model = watch[:-1], watch[-1:]
    print product + ' ' + model

def parsedecisec(dsec):
    hours = dsec / 36000;
    minutes = (dsec - (hours * 36000)) / 600
    seconds = (dsec - (hours * 36000) - (minutes * 600)) / 10
    dseconds = (dsec - (hours * 36000) - (minutes * 600) - (seconds * 10))
    return '%2.2d:%2.2d:%2.2d.%1d' % (hours, minutes, seconds, dseconds)

def trackfromhex(hex, timezone=utc):
    id = 0
    t = {}
    if len(hex) == 44 or len(hex) == 48:
        t['date'] = datetime.datetime(2000+Utilities.hex2dec(hex[0:2]),
                Utilities.hex2dec(hex[2:4]), Utilities.hex2dec(hex[4:6]),
                Utilities.hex2dec(hex[6:8]), Utilities.hex2dec(hex[8:10]),
                Utilities.hex2dec(hex[10:12]), tzinfo=timezone)
        # Endianess is different in this devicea
        t['trackpoints'] = int(hex[14:16] + hex[12:14], 16)
        t['duration'] = int(hex[18:20] + hex[16:18], 16)
        t['distance'] =  int(hex[22:24] + hex[20:22] + hex[26:28] + hex[24:26], 16)
        #track['calories'] = Utilities.hex2dec(hex[28:32])
        #track['count'] = Utilities.hex2dec(hex[36:44])
        t['laps'] = Utilities.hex2dec(hex[30:34])
        t['id'] = Utilities.hex2dec(hex[38:42])
    print 'raw track: ' + str(hex)
    print 'id ' + str(t['id']) + ' date ' + str(t['date']) + ' duration ' + \
            parsedecisec(t['duration']) + ' distance ' + str(t['distance']) + \
            ' trackpoints ' + str(t['trackpoints']) \
            +' laps ' + str(t['laps'])
    return t

def gettracklist():
    writeserial('getTracklist')
    tracklist = readserial()
    if len(tracklist) > 8:
        tracks = Utilities.chop(tracklist[6:-2],48)#trim header, wtf?
        print '%i tracks found' % len(tracks)
        for track in tracks:
            trackfromhex(track)

def gettracks(trackids):
    gdata = ''
    trackids = [Utilities.dec2hex(str(id), 4) for id in trackids]
    payload = Utilities.dec2hex((len(trackids) * 512) + 896, 4)
    numberoftracks = Utilities.dec2hex(len(trackids), 4)
    checksum = Utilities.checkersum("%s%s%s" %
                    (payload, numberoftracks, ''.join(trackids)))
    writeserial('getTracks', **{'payload':payload,
        'numberOfTracks':numberoftracks, 'trackIds':''.join(trackids),
        'checksum':checksum})
#    while(True)
    for i in range(30):
        data = readserial(2075)
        writeserial('requestNextTrackSegment')
        gdata += data
    return gdata

#    while True:
#        data = self._readSerial(2075)
#        time.sleep(2)
usage = '''
Usage: gb850.py [-fi <input-format>] [-fo <output format>] convert <infile> <outfile>
                [-d <device>] list
                [-d <device>] [-fo <output format>] extract <outfile>

                [-i <input file>] [-d <device>] [-fi <input-format>] [-fo <output-format>]
                [-
                [-i <inputfile>] [-O
<outputfile>]
       formats: GPX FCX ACT
       if format is ommited, FCX is select by default
       if input file is ommited, the device is used
       if output file is ommited, stdout is used
'''
if __name__=="__main__":
    parser = optparse.OptionParser()
    parser.add_option("-f", "--output-format", dest="output-format", default="FCX",
                      help="Output format. If ommited, 'FCX'")
    parser.add_option("-F", "--input-format", dest="input-format", default="stdin",
                      help="Use <filename> as input file. If ommited, use stdin.",
                      metavar="FILE")
    parser.add_option("-o", "--output", dest="output", default="stdout",
                      help="Use <filename> as output file. If ommited, use stdout.",
                      metavar="FILE")
    parser.add_option("-i", "--input", dest="input", default="stdin",
                      help="Use <filename> as output file. If ommited, use the device itself",
                      metavar="FILE")
    parser.add_option("-d", "--device", dest="device", default="/dev/ttyACM0",
                      help="Use <device> as serial port for the GB850P, if \
ommited, use /dev/ttyACM0... Find out with dmesg")

print 'Opening serial port at /dev/ttyACM0, 57600 bauds...'
serial = serial.Serial(port='/dev/ttyACM0', baudrate='57600',
    timeout=2)

# writeserial('whoAmI')
# print readserial()

getmodel()

gettracklist()

track7 = gettracks([0])

print track7

