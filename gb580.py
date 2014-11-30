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

import sys
import serial, datetime, time, optparse
from pytz import timezone, utc
from decimal import Decimal
from dateutil import parser #needs python-dateutil on Ubuntu
from datetime import timedelta

DEBUG = False
TRACK_HEADER_LEN = 48   # 24bytes
TRACK_POINT_LEN = 64    # 32bytes
TRACK_LAP_LEN   = 80    # 40bytes
TRACKPTS_PER_SECTION = 63
SECTION_LEN = 4080 # TRACK_HEADER_LEN + TRACKPTS_PER_SECTION*TRACK_POINT_LEN (in bytes)
act_time = None

class Utilities():
    """Contains several conversion utility functions"""

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
        '''chops the input string into chunk length segments'''
        return [s[i * chunk : (i+1) * chunk] for i in range((len(s) + chunk - 1) / chunk)]

    @classmethod
    def checkersum(self, hex):
        checksum = 0

        for i in range(0, len(hex), 2):
            checksum = checksum ^ int(hex[i:i+2], 16)
        return self.dec2hex(checksum)

    @classmethod
    def get_app_prefix(self, *args):
        ''' Return the location the app is running from'''
        is_frozen = False
        try:
            is_frozen = sys.frozen
        except AttributeError:
            pass
        if is_frozen:
            app_prefix = os.path.split(sys.executable)[0]
        else:
            app_prefix = os.path.split(os.path.abspath(sys.argv[0]))[0]
        if args:
            app_prefix = os.path.join(app_prefix, *args)
        return app_prefix

    @classmethod
    def read_int16(self, hex):
        ret = self.hex2dec(hex[2:4] + hex[0:2])
        return ret

    @classmethod
    def read_int32(self, hex):
        ret = self.hex2dec(hex[6:8] + hex[4:6] + hex[2:4] + hex[0:2])
        return ret

    @classmethod
    def read_datetime(self, hex, timezone):
        return datetime.datetime(2000 + self.hex2dec(hex[0:2]),
            self.hex2dec(hex[2:4]), self.hex2dec(hex[4:6]),
            self.hex2dec(hex[6:8]), self.hex2dec(hex[8:10]),
            self.hex2dec(hex[10:12]), tzinfo=timezone)



class Serial():
    """Basic API for serial port read/write operations"""

    def write_serial(self, command, *args, **kwargs):
        hex = self.COMMANDS[command] % kwargs
        if DEBUG:
            print 'writing to serialport: %s %s' % (command, hex)
        serial.write(Utilities.hex2chr(hex))
        #time.sleep(2)
        if DEBUG:
            print 'waiting at serialport: %i' % serial.inWaiting()


    def read_serial(self, size = 2070):
        data = Utilities.chr2hex(serial.read(size))
        if DEBUG:
            print 'serial port returned: %s' % data if len(data) < 30 else '%s... (truncated)' % data[:30]
            #print 'serial port returned: %s' % data
        return data


class TrackPoint:
    """This class holds one trackpoint, with all auxilliary data available"""
    '''
    TrackPointLength = 32

    FDE0 C102 D089 3301 4D00 0000 D807 0000 7800 0000 1E00 0000 5600 0000 0000 0000
    0    2    4    6    8    10   12   14   16   18   20   22   24   26   28   30   32
    0   0       FDE0C102        02C1E0FD        46260477        Latitude        46,260477
    4   8       D0893301        013389D0        20154832        Longitude       20,154832
    8   16      4D00            004D            4*16+13*1=77    Altitude        77 m
    10  20      0000                                            2 byte padding
    12  24      D8070000        000007D8        2008            Speed           20,08 km/h
    16  32      78              78              7*16+8*1=120    HeartRate       120 /min
    17  34      000000                                          3 byte padding
    20  40      1E000000        0000001E        1*16+14=30      IntervalTime    3.0s (30/10)
    24  48      5600            0056            5*16+6*1=86     Cadence         86 /min
    26  52      0000                                            PowerCadence    0
    28  56      0000                                            Power           0
    30  60      0000                                            2 byte padding
    32  64....
    '''

    def __init__(self):
        self.latitude       = None  # [+N, -S]
        self.longitude      = None  # [+E, -W]
        self.altitude       = None  # [m]
        self.speed          = None  # [km/h]
        self.hr             = None  # [1/min]
        self.interval_time  = None  # [s]
        self.timestamp      = None  # [absolute time]
        self.cadence        = None  # [1/min]
        self.power_cad      = None
        self.power          = None  # [W]

    def process_trackpoint(self, data, act_time):
        self.latitude = Utilities.read_int32(data[0:]) / 1000000.0
        self.longitude = Utilities.read_int32(data[8:]) / 1000000.0
        self.altitude = Utilities.read_int16(data[16:])
        self.speed = Utilities.read_int32(data[24:]) / 100.0
        self.hr = int(data[32:34], 16)
        self.interval_time = Utilities.read_int32(data[40:]) / 10.0
        self.cadence = Utilities.read_int16(data[48:])
        self.power_cad = Utilities.read_int16(data[52:])
        self.power = Utilities.read_int16(data[56:])

        #Timestamp is an increment from the previous trackpoint
        act_time += timedelta(milliseconds = self.interval_time * 1000)
        self.timestamp = act_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        if DEBUG:
            print(self.latitude, self.longitude, self.altitude,
                self.speed, self.hr, self.interval_time,
                self.cadence, self.power_cad, self.power)
        return act_time

    def extension(self, temp):
        '''Compiles the GPX extension part of a trackpoint'''
        #if self.__opts['noext']:
        #    return ""

        extension_found = False

        hr_ext = ""
        if (self.hr is not None):
            extension_found = True
            hr_ext = "<gpxtpx:hr>{hr}</gpxtpx:hr>".format(hr=self.hr)

        tmp_ext = ""
        #if ((not self.__opts['notemp']) and (temperature is not None)):
        if (temp is not None):
            extension_found = True
            tmp_ext = "<gpxtpx:atemp>{temp}</gpxtpx:atemp>".format(
                                                    temp=temp)

        cad_ext = ""
        if (self.cadence is not None):
            extension_found = True
            cad_ext = "<gpxtpx:cad>{cad}</gpxtpx:cad>".format(
                                                    cad=self.cadence)

        pow_ext = ""
        #if ((not self.__opts['nopower']) and (power is not None)):
        if (self.power is not None):
            extension_found = True
            pow_ext = "<gpxtpx:power>{pwr}</gpxtpx:power>".format(
                                                    pwr=self.power)

        if not extension_found:
            return ""

        #Compose return string
        ret = """<extensions>
        <gpxtpx:TrackPointExtension>
            {hrext}""".format(hrext=hr_ext)

        if tmp_ext != "":
            ret += """
            {tmpext}""".format(tmpext=tmp_ext)

        if pow_ext != "":
            ret += """
            {powext}""".format(powext=pow_ext)

        if cad_ext != "":
            ret += """
            {cadext}""".format(cadext=cad_ext)

        ret += """
        </gpxtpx:TrackPointExtension>
    </extensions>"""

        return ret


    def write_gpx(self):
        '''Writes the data to a GPX trackpoint structure'''
        temperature = None
        ret = \
"""
<trkpt lat="{latitude}" lon="{longitude}"><ele>{altitude}</ele><time>{time}</time><speed>{speed}</speed>
    {extension}
</trkpt>
""".format(latitude=self.latitude, longitude=self.longitude,
            altitude=self.altitude, time=self.timestamp, speed=self.speed,
            extension=self.extension(temperature))
        return ret


class TrackLap:
    """This class holds one lap's data"""
    '''
    The N x lap data is preceeded by a TrackHeader structure
    0E0A1D122A2C 3607 6498 0000 1E76 0000 0800 0000 0700 AA00 5916 0000 5916 0000 6E0E 0000 5100 0000 1A0E 0000 957D 8700 8700 5F00 6900 0000 0000 0000 0000 0E01 DA38 0000 8122 0000 CE1C 0000 AB00 0000 D30E 0000 A997 8600 8700 5B00 6B00 0000 0000 0000 0E01 B002 884B 0000 AE12 0000 B90F 0000 6500 0000 270E 0000 A8A2 8600 8600 4D00 5900 0000 0000 0000 B002 9203 6D56 0000 E50A 0000 2608 0000 3200 0000 200B 0000 A58F 8600 8600 5400 5B00 0000 0000 0000 9203 1604 2569 0000 B812 0000 DA10 0000 6C00 0000 6410 0000 B1AA 8600 8600 5300 6A00 0000 0000 0000 1604 F804 8F74 0000 6A0B 0000 3B08 0000 3700 0000 FA0B 0000 B093 8600 8600 5800 6400 0000 0000 0000 F804 8205 8587 0000 F612 0000 6F0F 0000 6F00 0000 6011 0000 B4AD 8600 8600 4F00 6100 0000 0000 0000 8205 6706 6498 0000 DF10 0000 7B0A 0000 4E00 0000 980A 0000 B490 8600 8600 5800 6400 0000 0000 0000 6706 3507 65
    0            6    8    10   12   14   16   18   20   22   24   26   28   30   32   34   36   38   40   42   44   46   48   50   52   54   56   58   60   62   64   66   68   70   72   74   76   78   80   82   84   86   88   90   92   94   96   98   100  102  104  106  108  110  112  114  116  118  120  122  124  126  128
    0       0E      14      year
            0A      10      month
            1D      29      day
            12      18      hour
            2A      42      minutes
            2C      44      seconds
    6       3607    0736    0*4096+7*256+3*16+6=1846        TrackPointCount
    8       6498    9864    9*4096+8*256+6*16+4=39012       Totaltime
    10      0000
    12      1E76    761E    7*4096+6*256+1*16+14=30238      TotalDistanceMeters
    14      0000
    16      0800    0008    0*4096+0*256+0*16+8=8           # of laps
    18      0000
    20      0700    0007    0*4096+0*256+0*16+7=7           ???
    22      AA00    00AA    0*4096+0*256+10*16+10=170       ???
    ---Lap info starts here---
    24      5916
    26      0000    00001659 1*4096+6*256+5*16+9=5721       EndTime 572.1sec = 9m 32.1s
    28      5916
    30      0000    00001659 1*4096+6*256+5*16+9=5721       LapTime 572.1sec = 9m 32.1s
    32      6E0E
    34      0000    00000E6E 0*4096+E*256+6*16+E=3694       LapDistanceMeters 3694m
    36      5100    0051    0*4096+0*256+5*16+1=81          LapCalories 81Cal
    38      0000                                            2-byte pad
    40      1A0E
    42      0000    00000E1A    0*4096+14*256+1*16+10=3610  MaximumSpeed 36100 m/h
    44      95      95      9*16+5=157                      MaximumHeartRate
    45      7D      7D      7*16+13=125                     AverageHeartRate
    46      8700    0087    0*4096+0*256+8*16+7=135         MinimumAltitude
    48      8700    0087    0*4096+0*256+8*16+7=135         MaximumAltitude
    50      5F00    005F    0*4096+0*256+5*16+15=95         AverageCadence
    52      6900    0069    0*4096+0*256+6*16+9=105         MaximumCadence
    54      0000                                            AveragePower
    56      0000                                            MaximumPower
    58      0000                                            2-byte pad
    60      0000                                            StartPointIndex
    62      0E01    010E    0*4096+1*256+0*16+14=270        EndPointIndex
    '''

    def __init__(self):
        self.end_time       = None  # [s] Seconds from workout start
        self.lap_time       = None  # [s] Seconds from lap start
        self.distance       = None  # [m]
        self.calories       = None  # [kCal]
        self.max_speed      = None  # [km/h]
        self.max_hr         = None  # [1/min]
        self.avg_hr         = None  # [1/min]
        self.min_altitude   = None  # [m]
        self.max_altitude   = None  # [m]
        self.avg_cadence    = None  # [1/min]
        self.max_cadence    = None  # [1/min]
        self.avg_power      = None  # [W]
        self.max_power      = None  # [W]
        self.start_pt_index = None  # [idx]
        self.end_pt_index   = None  # [idx]

    def process_lap(self, data):
        self.end_time = Utilities.read_int32(data[0:]) / 10.0
        self.lap_time = Utilities.read_int32(data[8:]) / 10.0
        self.distance = Utilities.read_int32(data[16:])
        self.calories = Utilities.read_int16(data[24:])
        self.max_speed = Utilities.read_int32(data[32:]) / 100.0
        self.max_hr = int(data[40:42], 16)
        self.avg_hr = int(data[42:44], 16)
        self.min_altitude = Utilities.read_int16(data[44:])
        self.max_altitude = Utilities.read_int16(data[48:])
        self.avg_cadence = Utilities.read_int16(data[52:])
        self.max_cadence = Utilities.read_int16(data[56:])
        self.avg_power = Utilities.read_int16(data[60:])
        self.max_power = Utilities.read_int16(data[64:])
        self.start_pt_index = Utilities.read_int16(data[72:])
        self.end_pt_index = Utilities.read_int16(data[76:])

        if DEBUG:
            print(self.end_time, self.lap_time, self.distance,
                self.calories, self.max_speed, self.max_hr,
                self.avg_hr, self.min_altitude, self.max_altitude,
                self.avg_cadence, self.max_cadence, self.avg_power,
                self.max_power, self.start_pt_index, self.end_pt_index)
        return TRACK_LAP_LEN

    def write_gpx(self):
        return ""


class GB580(Serial):
    """API for Globalsat GB580"""

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

    def __init__(self):
        self.track_laps = []
        self.track_points = []

    def get_startdate(self):
        '''Returns the track start date as a string, eg 20141231'''
        return self.act_time.strftime("%Y%m%d")

    def get_model(self):
        '''Reads and displays the GPS unit's model & version'''
        self.write_serial('whoAmI')
        response = self.read_serial()
        watch = Utilities.hex2chr(response[6 : -4])
        product, model = watch[ : -1], watch[-1 : ]
        print 'watch: %s, product: %s, model: %s' % (watch, product, model)

    def read_tracklist(self):
        '''Reads the complete track list'''
        self.write_serial('getTracklist')
        tracklist = self.read_serial()
        if len(tracklist) > 8: #string len is > 8 so not an error code
            return self.process_tracklist(tracklist)

    def process_tracklist(self, tracklist, timezone=timezone('Europe/Budapest')):
        '''
        The tracklist only contains basic information about the tracks:
        id, date, time, duration, laps

        0E0A1D122A2C B806 248D 0000 695B 0000 0100 CA00 0800 0000 E8
        0            6    8    10   12   14   16   18   20   22
        0       0E      14      year
                0A      10      month
                1D      29      day
                12      18      hour
                2A      42      minutes
                2C      44      seconds
        6       B806    06B8    1720        TrackPointCount
        8       248D
        10      0000    00008D24 36132      TotalTime   3613.2s
        12      695B
        14      0000    00005B69 23401      TotalDistanceMeters 23401m
        16      0100    0001    1           LapCount
        18      CA00    00CA    202         TrackPointIndex
        20      0800    0008    8           TrackId, starting from 0
        '''

        #trim 6-byte header and 2-byte footer,
        #then chop the string into 48-byte segments,
        #each segment corresponds a track header
        tracks = Utilities.chop(tracklist[6 : -2], 48)
        #Print a list of track headers
        print '%i tracks found' % len(tracks)
        print 'id           date            distance duration topspeed trkpnts  laps'
        for track in tracks:
            t = {}
            if len(track) == 44 or len(track) == 48:
                t['date'] = Utilities.read_datetime(track, timezone)
                t['trackpoints'] = Utilities.hex2dec(track[14:16] + track[12:14])
                t['duration'] = Utilities.hex2dec(track[20:22] + track[18:20] + track[16:18])
                t['laps'] = Utilities.hex2dec(track[30:34])
                t['id'] = Utilities.hex2dec(track[38:42])
                t['distance'] = Utilities.hex2dec(track[28:30] + track[26:28] + track[24:26])
                t['calories'] = 0   #Utilities.hex2dec(track[28:32])
                t['topspeed'] = 0   #Utilities.hex2dec(track[36:44])

            #~ print 'raw track: ' + str(track)
            print "%02i %s %08i %08i %08i %08i %04i" % \
                (t['id'], str(t['date']), t['distance'], t['duration'],
                 t['topspeed'], t['trackpoints'], t['laps'])

        return tracks

    def read_track(self, track_ids):
        track_ids = [Utilities.dec2hex(str(track_ids), 4)]
        payload = Utilities.dec2hex((len(track_ids) * 512) + 896, 4)
        num_of_tracks = Utilities.dec2hex(len(track_ids), 4)
        checksum = Utilities.checkersum("%s%s%s" %
                        (payload, num_of_tracks, ''.join(track_ids)))

        self.write_serial('getTracks',
            **{'payload':payload, 'numberOfTracks':num_of_tracks,
            'trackIds':''.join(track_ids), 'checksum':checksum})
        data = self.read_serial(2075)
        #time.sleep(2)
        self.process_track_header(data)

    def process_track_header(self, data):
        data = data[6:]
        self.start_time = Utilities.read_datetime(data, timezone('Europe/Budapest')) #timezone?
        self.track_pt_count = Utilities.read_int16(data[12:])
        self.total_time = Utilities.read_int32(data[16:]) / 10.0
        self.total_distance = Utilities.read_int32(data[24:])
        self.num_of_laps = Utilities.read_int16(data[32:])
        self.total_calories = Utilities.read_int16(data[48:])
        self.max_speed = Utilities.read_int32(data[56:]) / 100.0
        self.max_hr = int(data[64:66], 16)
        self.avg_hr = int(data[66:68], 16)
        self.total_ascend = Utilities.read_int16(data[68:]);
        self.total_descend = Utilities.read_int16(data[72:]);
        self.min_altitude = Utilities.read_int16(data[76:]);
        self.max_altitude = Utilities.read_int16(data[80:]);
        self.avg_cadence = Utilities.read_int16(data[84:]);
        self.max_cadence = Utilities.read_int16(data[88:]);
        self.avg_power = Utilities.read_int16(data[92:]);
        self.max_power = Utilities.read_int16(data[96:]);

        self.act_time = self.start_time

        if DEBUG:
            print(self.start_time,
                self.track_pt_count,
                self.total_time,
                self.total_distance,
                self.num_of_laps,
                self.total_calories,
                self.max_speed,
                self.max_hr,
                self.avg_hr,
                self.total_ascend,
                self.total_descend,
                self.min_altitude,
                self.max_altitude,
                self.avg_cadence,
                self.max_cadence,
                self.avg_power,
                self.max_power)
        return

    def read_laps(self):
        self.write_serial('requestNextTrackSegment')
        #data = "8001580E0A1D122A2C3607649800001E760000080000000700AA0059160000591600006E0E0000510000001A0E0000957D870087005F00690000000000000000000E01DA38000081220000CE1C0000AB000000D30E0000A997860087005B006B000000000000000E01B002884B0000AE120000B90F000065000000270E0000A8A2860086004D005900000000000000B00292036D560000E50A00002608000032000000200B0000A58F8600860054005B000000000000009203160425690000B8120000DA1000006C00000064100000B1AA8600860053006A000000000000001604F8048F7400006A0B00003B08000037000000FA0B0000B0938600860058006400000000000000F804820585870000F61200006F0F00006F00000060110000B4AD860086004F0061000000000000008205670664980000DF1000007B0A00004E000000980A0000B49086008600580064000000000000006706350765"
        data = self.read_serial(2075)
        #time.sleep(2)
        # chop off first 3 bytes, status + # of bytes received
        data = data[6:]
        offset = TRACK_HEADER_LEN
        while offset <= len(data) - TRACK_LAP_LEN:
            tl = TrackLap()
            offset += tl.process_lap(data[offset:])
            self.track_laps.append(tl)

        if DEBUG:
            print len(self.track_laps)
        return len(self.track_laps)

    def read_trackpoints(self):
        self.write_serial('requestNextTrackSegment')
        while True:
            data = self.read_serial(2075)
            # chop off first 3 bytes, status + # of bytes received
            data = data[6:]

            # Process this chunk of data received,
            # contains a header and 0..SECTION_LEN trackpoints
            offset = TRACK_HEADER_LEN
            while offset <= len(data) - TRACK_POINT_LEN:
                tp = TrackPoint()
                self.act_time = tp.process_trackpoint(data[offset:], self.act_time)
                self.track_points.append(tp)
                offset += TRACK_POINT_LEN
                if len(self.track_points) % 100 == 0:
                    sys.stdout.write(".")
                if len(self.track_points) % (80*100) == 0:
                    sys.stdout.write("\n")

            if len(data) - 2 == SECTION_LEN: # last 2 bytes are status
                self.write_serial('requestNextTrackSegment')
            else:
                break
        sys.stdout.write("\n")
        if DEBUG:
            print len(self.track_points)
        return len(self.track_points)

    def write_gpx_header(self, outputfile):
        self.__outputfile = output_file
        #Write GPX header
        #Creator set to Garmin Edge 800 so that Strava accepts
        # barometric altitude datae
        print >> self.__outputfile, \
            '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'
        print >> self.__outputfile, """
<gpx version="1.1"
creator="Garmin Edge 800"
xmlns="http://www.topografix.com/GPX/1/1"
xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd">

  <metadata>
    <link href="https://github.com/kgkilo/gb580">
      <text>gb580.py</text>
    </link>
  </metadata>

  <trk>
    <trkseg>
"""

    def write_track(self):
        for lap in self.track_laps:
            print >> self.__outputfile, lap.write_gpx()
        for pt in self.track_points:
            print >> self.__outputfile, pt.write_gpx()

        #Finish writing GPX file
        print >> self.__outputfile,"""
    </trkseg>
  </trk>
</gpx>
"""


def parsedecisec(dsec):
    hours = dsec / 36000;
    minutes = (dsec - (hours * 36000)) / 600
    seconds = (dsec - (hours * 36000) - (minutes * 600)) / 10
    dseconds = (dsec - (hours * 36000) - (minutes * 600) - (seconds * 10))
    return '%2.2d:%2.2d:%2.2d.%1d' % (hours, minutes, seconds, dseconds)


usage = '''
Usage: gb580.py [-fi <input-format>] [-fo <output format>] convert <infile> <outfile>
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

    gb = GB580()
    print 'Opening serial port at /dev/ttyACM0, 57600 bauds...'
    serial = serial.Serial(port='/dev/ttyACM0', baudrate='57600',
        timeout=2)

    gb.get_model()                  # Just for info
    tracks = gb.read_tracklist()    # List all tracks in memory
    track = gb.read_track(29)       # Read one track
    gb.read_laps()                  # Read the track laps
    gb.read_trackpoints()           # Read the trackpoints

    root_filename = gb.get_startdate()
    output_filename = root_filename + '.gpx'
    output_file = open(output_filename, 'w')
    print "Creating file {0}".format(output_filename)
    gb.write_gpx_header(output_filename)
    gb.write_track()
    output_file.close()
