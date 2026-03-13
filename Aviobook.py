import ssl
import os
import html as _html_escape
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import pytz

def fetch_xml_from_api(username):
    import urllib.parse
    # SimBrief accepts ?username= (pilot ID alias) or ?userid= (numeric ID).
    param = "userid" if str(username).strip().isdigit() else "username"
    url = f"https://www.simbrief.com/api/xml.fetcher.php?{param}={urllib.parse.quote(str(username).strip())}"

    # Build SSL context — try certifi first (Railway/Linux), then the macOS
    # bundle installed by "Install Certificates.command", then fall back to
    # the system default.  Never disable verification entirely.
    def _make_ssl_context():
        # 1. certifi (installed via pip, works everywhere including Railway)
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        # 2. macOS: /Applications/Python 3.x/Install Certificates.command
        #    installs certs into the Python framework's openssl store.
        #    If that ran, create_default_context() already finds them.
        #    But if not, the macOS system keychain is at a known path.
        import platform, os
        if platform.system() == "Darwin":
            mac_cafile = "/etc/ssl/cert.pem"          # macOS system bundle
            if os.path.exists(mac_cafile):
                return ssl.create_default_context(cafile=mac_cafile)
        # 3. Standard default (works on most Linux distros including Railway)
        return ssl.create_default_context()

    context = _make_ssl_context()
    try:
        with urllib.request.urlopen(url, context=context) as response:
            data = response.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SimBrief API returned HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach SimBrief API: {e.reason}") from e

    # SimBrief returns HTTP 200 even for errors — check the XML status field
    try:
        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(data)
        status = root.findtext('fetch/status') or root.findtext('status') or ''
        if status.lower() not in ('ok', 'success', ''):
            msg = root.findtext('fetch/message') or root.findtext('message') or status
            raise RuntimeError(f"SimBrief error: {msg}")
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError("SimBrief returned an invalid response (not XML). Check your username/ID.")

    return data



def format_time_pair(unix_time, offset_str):
    try:
        timestamp = int(unix_time)
        utc_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        utc_str = utc_dt.strftime('%H:%M')
        offset_hours = int(offset_str) if offset_str else 0
        local_dt = utc_dt + timedelta(hours=offset_hours)
        local_str = local_dt.strftime('%H:%M')
        return utc_str, local_str
    except Exception as e:
        print(f"[Time Format Error] unix_time={unix_time}, offset={offset_str}, error={e}")
        return "N/A", "N/A"


def format_time_display(time_str):
    """Format time string (HHMM) to HH:MM format for display"""
    if time_str and len(time_str) >= 4:
        return f"{time_str[:2]}:{time_str[2:4]}"
    return time_str


def sec_to_hhmm(sec):
    try:
        sec_int = int(sec)
        h = sec_int // 3600
        m = (sec_int % 3600) // 60
        return f"{h:02d}{m:02d}"
    except Exception:
        return "0000"


def parse_simbrief_xml(xml_data):
    import xml.etree.ElementTree as ET
    from datetime import datetime, timedelta

    root = ET.fromstring(xml_data)
    data = {}

    def get(tag, default=""):
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else default


    def get_all(tag):
        return [el.text.strip() for el in root.findall(tag) if el.text]

    def get_bucket_data():
        return [
            {
                'label': b.findtext('label'),
                'fuel': int(b.findtext('fuel') or 0),
                'time': int(b.findtext('time') or 0),
                'required': b.findtext('required') is not None
            }
            for b in root.findall('fuel/fuel_extra/bucket')
            if int(b.findtext('fuel') or 0) > 0
        ]

        

    def get_airport_info(prefix):
        def try_tags(node, *tags):
            for t in tags:
                el = node.find(t)
                if el is not None and el.text:
                    return el.text.strip()
            return ''
        if prefix == 'orig':
            node = root.find('origin')
            if node is None:
                return {'icao': get('origin/orig_icao'), 'iata': get('origin/orig_iata'), 'name': get('origin/orig_name'), 'gate': get('origin/gate'), 'elevation': ''}
            return {
                'icao': try_tags(node, 'icao_code', 'orig_icao', 'icao'),
                'iata': try_tags(node, 'iata_code', 'orig_iata', 'iata'),
                'name': try_tags(node, 'name', 'orig_name'),
                'gate': try_tags(node, 'gate', 'orig_gate'),
                'elevation': try_tags(node, 'elevation', 'elev', 'orig_elevation'),
            }
        else:
            node = root.find('destination')
            if node is None:
                return {'icao': get('destination/dest_icao'), 'iata': get('destination/dest_iata'), 'name': get('destination/dest_name'), 'gate': get('destination/gate'), 'elevation': ''}
            return {
                'icao': try_tags(node, 'icao_code', 'dest_icao', 'icao'),
                'iata': try_tags(node, 'iata_code', 'dest_iata', 'iata'),
                'name': try_tags(node, 'name', 'dest_name'),
                'gate': try_tags(node, 'gate', 'dest_gate'),
                'elevation': try_tags(node, 'elevation', 'elev', 'dest_elevation'),
            }

    def get_alternates():
        result = []

        def parse_altn(node, altn_type):
            icao = node.findtext('icao_code', '') or node.findtext('icao', '')
            if not icao:
                return None
            return {
                'type': altn_type,
                'icao': icao,
                'iata': node.findtext('iata_code', '') or node.findtext('iata', ''),
                'name': node.findtext('name', ''),
                'burn': node.findtext('burn', '0'),
                'distance': node.findtext('distance', '0'),
                'ete': node.findtext('ete', '0'),
                'route': node.findtext('route', ''),
                'cruise_altitude': node.findtext('cruise_altitude', '0'),
                'elevation': node.findtext('elevation', ''),
                'trans_alt': node.findtext('trans_alt', ''),
                'trans_level': node.findtext('trans_level', ''),
                'metar': node.findtext('metar', ''),
                'metar_category': node.findtext('metar_category', ''),
                'taf': node.findtext('taf', ''),
                'timezone': node.findtext('timezone', ''),
            }

        # 1. Takeoff alternate
        toa = root.find('takeoff_altn')
        if toa is not None:
            entry = parse_altn(toa, 'TKOF')
            if entry: result.append(entry)

        # 2. Enroute alternate
        era = root.find('enroute_altn')
        if era is not None:
            entry = parse_altn(era, 'ENRTE')
            if entry: result.append(entry)

        # 3. Destination alternates
        for alt in root.findall('alternate'):
            entry = parse_altn(alt, 'DEST')
            if entry: result.append(entry)

        return result

    def get_navlog_data():
        """
        Build navlog fix list TOC &#8594; TOD (exclusive), mirroring FLITEBRIEF logic:
          - ET = time_total (cumulative seconds from departure) as HHMM
          - ATA / rem_fuel computed in JS after pilot enters takeoff time + fuel
          - cum_time_sec and cum_fuel_used stored as data-attributes for JS recalc
        """
        # &#9472;&#9472; Plan defaults passed to HTML entry form &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
        sched_off_ts  = get('times/sched_off', '0')
        orig_tz_hours = int(get('times/orig_timezone', '0'))
        try:
            dt_utc   = datetime.fromtimestamp(int(sched_off_ts), tz=timezone.utc)
            dt_local = dt_utc + timedelta(hours=orig_tz_hours)
            sched_off_hhmm = dt_utc.strftime('%H%M')   # HHMM UTC, no colon
        except Exception:
            sched_off_hhmm = '0000'

        plan_ramp_str = get('fuel/plan_ramp', '0')
        plan_ramp     = int(float(plan_ramp_str or '0'))

        # &#9472;&#9472; Walk fixes TOC &#8594; TOD (inclusive) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
        navlog_fixes = []
        toc_reached  = False

        # First pass: collect all fixes to compute total distance for DTG
        all_fixes_raw = root.findall('navlog/fix')
        total_dist_nm = 0
        cum_dist_map = {}  # ident -> cumulative nm at that fix
        _cum = 0
        for fx in all_fixes_raw:
            d = fx.findtext('distance', '') or ''
            try:
                _cum += float(d)
            except Exception:
                pass
            cum_dist_map[fx.findtext('ident','')] = _cum
        total_dist_nm = _cum

        for fix in root.findall('navlog/fix'):
            ident = fix.findtext('ident', '')

            if ident == 'TOC' or ident.startswith('TOC/'):
                toc_reached = True
                cum_time_sec  = int(fix.findtext('time_total',    '0') or 0)
                cum_fuel_used = int(fix.findtext('fuel_totalused', '0') or 0)
                cum_dist_fix  = cum_dist_map.get(ident, 0)
                dtg_toc = round(total_dist_nm - cum_dist_fix)
                navlog_fixes.append({
                    'ident': ident, 'airway': fix.findtext('via_airway', ''),
                    'track_mag': fix.findtext('track_mag', '---'),
                    'heading_mag': fix.findtext('heading_mag', '---'),
                    'track_true': '', 'mag_course': '',
                    'altitude': fix.findtext('altitude_feet', ''), 'wind': '', 'ind_true': '',
                    'mach': '', 'dist': '', 'dist_to_go': str(dtg_toc) if dtg_toc else '',
                    'seg_fuel': '', 'seg_time': '', 'temperature': '', 'trp': '',
                    'et_hhmm': sec_to_hhmm(cum_time_sec),
                    'cum_time_sec': cum_time_sec, 'cum_fuel_used': cum_fuel_used,
                    'is_toc': True, 'is_tod': False,
                    'fir': fix.findtext('fir', ''),
                    'fir_name': fix.findtext('fir_name', ''),
                    'mora': fix.findtext('grid_mora', '') or fix.findtext('mora', ''),
                })
                continue

            if not toc_reached:
                continue

            is_tod = ident == 'TOD' or ident.startswith('TOD/')

            cum_time_sec  = int(fix.findtext('time_total',    '0') or 0)
            cum_fuel_used = int(fix.findtext('fuel_totalused', '0') or 0)
            et_hhmm = sec_to_hhmm(cum_time_sec)

            mach_raw = fix.findtext('mach', '0') or '0'
            try:
                mach_val = float(mach_raw)
                mach_display = f"M{mach_val:.2f}".replace("M0.", "M.")
            except ValueError:
                mach_display = mach_raw

            # DTG: prefer XML field, fall back to computed from total distance
            dtg_xml = fix.findtext('dist_to_go', '') or fix.findtext('distance_to_go', '')
            if not dtg_xml:
                cum_dist_fix = cum_dist_map.get(ident, 0)
                dtg_computed = total_dist_nm - cum_dist_fix
                dtg_xml = str(round(dtg_computed)) if dtg_computed > 0 else ''

            # DIST: segment distance
            dist_raw = fix.findtext('distance', '')

            navlog_fixes.append({
                'ident':        ident,
                'airway':       fix.findtext('via_airway',    ''),
                'track_mag':    fix.findtext('track_mag',     '---'),
                'heading_mag':  fix.findtext('heading_mag',   '---'),
                'track_true':   fix.findtext('track_true',    ''),
                'mag_course':   fix.findtext('mag_course',    ''),
                'altitude':     fix.findtext('altitude_feet', '---'),
                'wind':         f"{fix.findtext('wind_dir','')}/{fix.findtext('wind_spd','')}",
                'ind_true':     f"{fix.findtext('ind_airspeed','---')}/{fix.findtext('true_airspeed','---')}",
                'mach':         mach_display,
                'dist':         dist_raw,
                'dist_to_go':   dtg_xml,
                'seg_fuel':     fix.findtext('fuel_flow',     '') or fix.findtext('fuel_seg', ''),
                'seg_time':     fix.findtext('time_leg',      '') or fix.findtext('time_seg', ''),
                'temperature':  fix.findtext('oat',           '') or fix.findtext('temperature', ''),
                'trp':          fix.findtext('true_airspeed', ''),
                'et_hhmm':      et_hhmm,
                'cum_time_sec': cum_time_sec,
                'cum_fuel_used':cum_fuel_used,
                'is_toc':       False,
                'is_tod':       is_tod,
                'fir':          fix.findtext('fir', ''),
                'fir_name':     fix.findtext('fir_name', ''),
                'mora':         fix.findtext('grid_mora', '') or fix.findtext('mora', ''),
            })

            if is_tod:
                break

        return navlog_fixes, plan_ramp, sched_off_hhmm

    images_node = root.find("images")
    if images_node is not None:
        directory = images_node.findtext("directory", "")
        maps = images_node.findall("map")
        images = []
        for m in maps:
            name = m.findtext("name", "No Name")
            link = m.findtext("link", "")
            full_link = directory + link
            images.append({"name": name, "link": full_link})
        data['images'] = images
    else:
        data['images'] = []

    # Handle files with the same directory structure
    files_node = root.find("files")
    if files_node is not None:
        directory = files_node.findtext("directory", "")
        pdf_nodes = files_node.findall("pdf")
        files = []
        for pdf in pdf_nodes:
            name = pdf.findtext("name", "No Name")
            link = pdf.findtext("link", "")
            full_link = directory + link
            files.append({"name": name, "link": full_link})
        data['files'] = files
    else:
        data['files'] = []
        
    # Get navlog data
    navlog_fixes, plan_ramp, sched_off_hhmm = get_navlog_data()
    
    # Timezone offsets
    orig_timezone = get('times/orig_timezone', '0')
    dest_timezone = get('times/dest_timezone', '0')


    def safe_format_time(raw_val, tz):
        try:
            if isinstance(raw_val, (list, tuple)):
                raw_val = raw_val[0]
            return format_time_pair(raw_val, tz)
        except Exception as e:
            print(f"safe_format_time error: {e}")
            return "N/A", "N/A"

    sched_off = safe_format_time(get('times/sched_off'), orig_timezone)
    est_out   = safe_format_time(get('times/est_out'), orig_timezone)
    sched_in  = safe_format_time(get('times/sched_in'), dest_timezone)
    est_in    = safe_format_time(get('times/est_in'), dest_timezone)
    sched_time_enroute = sec_to_hhmm(get('times/sched_time_enroute'))
    est_time_enroute = sec_to_hhmm(get('times/est_time_enroute'))



    # Flight duration
    try:
        block_time_secs = int(get('times/est_block', 0))
        hours = block_time_secs // 3600
        minutes = (block_time_secs % 3600) // 60
        duration_str = f"{hours}h{minutes:02d}min"
    except Exception:
        duration_str = "N/A"

    data.update({
        'general': {
            'icao_airline': get('general/icao_airline'),
            'flight_number': get('general/flight_number'),
            'cruise_profile': get('general/cruise_profile'),
            'avg_temp_dev': get('general/avg_temp_dev'),
            'avg_tropopause': get('general/avg_tropopause'),
            'avg_wind_dir': get('general/avg_wind_dir'),
            'avg_wind_spd': get('general/avg_wind_spd'),
            'avg_wind_comp': get('general/avg_wind_comp'),
            'route_distance': get('general/route_distance'),
            'air_distance': get('general/air_distance'),
            'initial_altitude': get('general/initial_altitude'),
            'cost_index': get('general/costindex'),
            'passengers': get('general/passengers'),
            'cargo': get('weights/cargo'),
            'payload': get('weights/payload'),
            'dx_rmk': '\n'.join(get_all('general/dx_rmk')),
            'mel_cdl': get('general/mel_cdl'),
            'remarks': get('general/remarks'),
            'plan_number': get('general/plan_no') or get('general/ofp_number') or get('params/request_id', ''),
            'orig_timezone': orig_timezone,
            'dest_timezone': dest_timezone,
            'duration': duration_str,
            'ste': sec_to_hhmm(get('times/sched_time_enroute')),
            'ete': sec_to_hhmm(get('times/est_time_enroute')),
        },
        'airports': {
            'origin': get_airport_info('orig'),
            'destination': get_airport_info('dest'),
            'alternate': get_all('alternate/icao_code')
        },

        'route': {
            'route': get('atc/flightplan_text'),
            'route_ifps': get('atc/route_ifps'),
            'sid_ident': get('general/sid_ident'),
            'sid_trans': get('general/sid_trans'),
            'star_ident': get('general/star_ident'),
            'star_trans': get('general/star_trans'),
            'stepclimb_string': get('general/stepclimb_string'),
            'route_distance': get('general/route_distance'),
            'dep_rwy': get('origin/plan_rwy'),
            'arr_rwy': get('destination/plan_rwy'),
            'navlog': root.findtext('.//atc/route',''),
            # ATC flight plan fields
            'atc_id':        get('atc/callsign') or get('general/icao_airline') + get('general/flight_number'),
            'flight_rules':  get('atc/flight_rules', 'I'),
            'flight_type':   get('atc/flight_type', 'S'),
            'aircraft_icao': get('aircraft/icaocode') or get('aircraft/iata'),
            'wake_cat':      get('aircraft/wakecat', 'M'),
            'equipment':     get('atc/equip', '') or get('general/nav_equipped', ''),
            'transponder':   get('atc/transponder', '') or get('general/transponder', ''),
            'dep_time_atc':  '',  # filled from sched_off below
            'speed_atc':     get('atc/cruise_speed') or get('general/cruise_mach', ''),
            'level_atc':     get('atc/initial_alt') or get('general/initial_altitude', ''),
            'dest_icao':     get('destination/icao_code') or get('destination/dest_icao', ''),
            'eet_atc':       sec_to_hhmm(get('times/est_time_enroute', '0')),
            'alt1_atc':      get('alternate/icao_code', ''),
            'alt2_atc':      '',
            'other_info':    get('atc/other_info', ''),
            'endurance':     sec_to_hhmm(get('times/endurance', '0')),
            'pob':           get('weights/pax_count_actual', ''),
        },
        'times': {
            'sched_off': sched_off,
            'sched_off_ts': get('times/sched_off', '0'),
            'est_out': est_out,
            'sched_block': sec_to_hhmm(get('times/sched_block')),
            'est_block': sec_to_hhmm(get('times/est_block')),
            'sched_time_enroute': sec_to_hhmm(get('times/sched_time_enroute')),
            'est_time_enroute': sec_to_hhmm(get('times/est_time_enroute')),
            'sched_in': sched_in,
            'est_in': est_in,
            'taxi_out': get('times/taxi_out', '0')
        },
        'fuel': {
            'enroute_burn':   get('fuel/enroute_burn'),
            'contingency':    get('fuel/contingency'),
            'alternate_burn': get('fuel/alternate_burn'),
            'reserve':        get('fuel/reserve'),
            'etops':          get('fuel/etops'),
            'extra':          get('fuel/extra'),
            'ballast':        get('fuel/ballast', '0'),
            'min_takeoff':    get('fuel/min_takeoff'),
            'taxi_out':       get('fuel/taxi'),
            'plan_takeoff':   get('fuel/plan_takeoff'),
            'plan_ramp':      get('fuel/plan_ramp', '0'),
            'plan_landing':   get('fuel/plan_landing'),
            'block':          get('fuel/block'),
            'avg_fuel_flow':  get('fuel/avg_fuel_flow'),
            'fuel_extra':     get_bucket_data(),
            # Times
            't_enroute':   sec_to_hhmm(get('times/est_time_enroute')),
            't_contingency': sec_to_hhmm(get('times/contfuel_time')),
            't_alternate': sec_to_hhmm(get('alternate/ete')),
            't_reserve':   sec_to_hhmm(get('times/reserve_time')),
            't_etops':     sec_to_hhmm(get('times/etopsfuel_time')),
            't_extra':     sec_to_hhmm(get('times/extrafuel_time')),
            't_taxi':      sec_to_hhmm(get('times/taxi_out')),
        },
        'weights': {
            'oew':          get('weights/oew'),
            'ow':           get('weights/est_ramp'),
            'payload':      get('weights/payload'),
            'cargo':        get('weights/cargo'),
            'pax_count':    get('weights/pax_count_actual'),
            'zero_fuel':    get('weights/est_zfw'),
            'max_zfw':      get('weights/max_zfw'),
            'fob':          get('fuel/plan_ramp', '0'),
            'max_fw':       get('fuel/max_takeoff'),
            'takeoff':      get('weights/est_tow'),
            'max_tow':      get('weights/max_tow'),
            'max_tow_struct': get('weights/max_tow_struct'),
            'burn':         get('fuel/enroute_burn'),
            'landing':      get('weights/est_ldw'),
            'max_ldw':      get('weights/max_ldw'),
        },
        'crew': {
            'cpt': get('crew/cpt'),
            'fo': get('crew/fo'),
            'dx': get('crew/dx'),
            'pu': get('crew/pu'),
            'fa': get_all('crew/fa')
        },
        'aircraft': {
            'type': get('aircraft/name'),
            'reg': get('aircraft/reg'),
            'fin': get('aircraft/fin')
        },
        'ofp': {
            'company': get('params/company'),
            'time': get('general/release'),
            'name':  get('crew/dx')
        },
        'navlog': {
            'fixes': navlog_fixes,
            'plan_ramp': plan_ramp,
            'sched_off_hhmm': sched_off_hhmm,
            'flight_key': (get('general/icao_airline') + get('general/flight_number') + '_' + get('general/release', '0')).replace(' ', '').replace('/', '')
        },
        'alternate': get_alternates()
    })

    # &#9472;&#9472; Build HTML NOTAM and Weather sections &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    data['notams_html']   = _build_notams_html(root)
    data['weather_html']  = _build_weather_html(root)

    return data


# &#9472;&#9472; NOTAM helpers &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;

def _parse_notam_iso_date(date_str):
    """Parse ISO-ish date string, return datetime or None."""
    if not date_str:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ',
                '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str[:len(fmt)], fmt)
        except ValueError:
            continue
    # Try numeric unix timestamp
    try:
        ts = int(date_str)
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    return None


def _notam_is_expired(exp_dt):
    """Return True if expiry datetime has passed."""
    if exp_dt is None:
        return False
    return exp_dt < datetime.now(timezone.utc).replace(tzinfo=None)


_NOTAM_QCODE_CATEGORY = {
    "AA": "AIRSPACE", "AC": "AIRSPACE", "AD": "AIRSPACE", "AE": "AIRSPACE",
    "AF": "AIRSPACE", "AH": "AIRSPACE", "AL": "AIRSPACE", "AN": "AIRSPACE",
    "AO": "AIRSPACE", "AP": "AIRSPACE", "AR": "AIRSPACE", "AT": "AIRSPACE",
    "AU": "AIRSPACE", "AV": "AIRSPACE", "AX": "AIRSPACE", "AZ": "AIRSPACE",
    "CA": "COMMUNICATION", "CB": "COMMUNICATION", "CC": "COMMUNICATION",
    "CD": "COMMUNICATION", "CE": "COMMUNICATION", "CG": "COMMUNICATION",
    "CL": "COMMUNICATION", "CM": "COMMUNICATION", "CP": "COMMUNICATION",
    "CR": "COMMUNICATION", "CS": "COMMUNICATION", "CT": "COMMUNICATION",
    "FA": "SERVICES", "FB": "SERVICES", "FC": "SERVICES", "FD": "SERVICES",
    "FE": "SERVICES", "FF": "SERVICES", "FG": "SERVICES", "FH": "SERVICES",
    "FI": "SERVICES", "FJ": "SERVICES", "FL": "SERVICES", "FM": "SERVICES",
    "FO": "SERVICES", "FP": "SERVICES", "FS": "SERVICES", "FT": "SERVICES",
    "FU": "SERVICES", "FW": "SERVICES", "FZ": "SERVICES",
    "GA": "NAVIGATION AIDS", "GW": "NAVIGATION AIDS",
    "IC": "APPROACH PROCEDURES", "ID": "APPROACH PROCEDURES",
    "IG": "APPROACH PROCEDURES", "IL": "APPROACH PROCEDURES",
    "IM": "APPROACH PROCEDURES", "IN": "APPROACH PROCEDURES",
    "IO": "APPROACH PROCEDURES", "IS": "APPROACH PROCEDURES",
    "LA": "LIGHTING", "LB": "LIGHTING", "LC": "LIGHTING", "LD": "LIGHTING",
    "LE": "LIGHTING", "LF": "LIGHTING", "LG": "LIGHTING", "LH": "LIGHTING",
    "LI": "LIGHTING", "LJ": "LIGHTING", "LK": "LIGHTING", "LL": "LIGHTING",
    "LM": "LIGHTING", "LP": "LIGHTING", "LR": "LIGHTING", "LS": "LIGHTING",
    "LT": "LIGHTING", "LU": "LIGHTING", "LV": "LIGHTING", "LW": "LIGHTING",
    "LX": "LIGHTING", "LY": "LIGHTING", "LZ": "LIGHTING",
    "MA": "MOVEMENT AREA", "MB": "MOVEMENT AREA", "MC": "MOVEMENT AREA",
    "MD": "MOVEMENT AREA", "MG": "MOVEMENT AREA", "MH": "MOVEMENT AREA",
    "MK": "MOVEMENT AREA", "MM": "MOVEMENT AREA", "MN": "MOVEMENT AREA",
    "MO": "MOVEMENT AREA", "MP": "MOVEMENT AREA", "MR": "RUNWAY",
    "MS": "MOVEMENT AREA", "MT": "MOVEMENT AREA", "MU": "MOVEMENT AREA",
    "MW": "MOVEMENT AREA", "MX": "MOVEMENT AREA", "MY": "MOVEMENT AREA",
    "NA": "NAVIGATION AIDS", "NB": "NAVIGATION AIDS", "NC": "NAVIGATION AIDS",
    "ND": "NAVIGATION AIDS", "NF": "NAVIGATION AIDS", "NL": "NAVIGATION AIDS",
    "NM": "NAVIGATION AIDS", "NN": "NAVIGATION AIDS", "NO": "NAVIGATION AIDS",
    "NT": "NAVIGATION AIDS", "NV": "NAVIGATION AIDS",
    "OA": "SERVICES", "OB": "OBSTACLE", "OE": "SERVICES", "OL": "OBSTACLE",
    "PA": "PROCEDURES", "PB": "PROCEDURES", "PC": "PROCEDURES",
    "PD": "PROCEDURES", "PE": "PROCEDURES", "PF": "PROCEDURES",
    "PH": "PROCEDURES", "PI": "APPROACH PROCEDURES", "PK": "PROCEDURES",
    "PL": "PROCEDURES", "PM": "PROCEDURES", "PN": "PROCEDURES",
    "PO": "PROCEDURES", "PR": "PROCEDURES", "PT": "PROCEDURES",
    "PU": "APPROACH PROCEDURES", "PX": "PROCEDURES", "PZ": "PROCEDURES",
    "RA": "AIRSPACE RESTRICTIONS", "RD": "AIRSPACE RESTRICTIONS",
    "RM": "AIRSPACE RESTRICTIONS", "RO": "AIRSPACE RESTRICTIONS",
    "RP": "AIRSPACE RESTRICTIONS", "RR": "AIRSPACE RESTRICTIONS",
    "RT": "AIRSPACE RESTRICTIONS",
    "WA": "WARNING", "WB": "WARNING", "WC": "WARNING", "WD": "WARNING",
    "WE": "WARNING", "WF": "WARNING", "WG": "WARNING", "WH": "WARNING",
    "WJ": "WARNING", "WL": "WARNING", "WM": "WARNING", "WP": "WARNING",
    "WR": "WARNING", "WS": "WARNING", "WT": "WARNING", "WU": "WARNING",
    "WV": "WARNING", "WW": "WARNING", "WY": "WARNING", "WZ": "WARNING",
}


_QCODE_SUBJECT_EXACT = {
    'Runway': 'RUNWAY', 'Taxiway': 'TAXIWAY', 'Apron': 'APRON',
    'Movement Area': 'APRON', 'Parking Area': 'APRON',
    'Bearing Strength': 'RUNWAY', 'Declared Distances': 'RUNWAY',
    'Threshold': 'RUNWAY', 'Stopway': 'RUNWAY', 'Clearway': 'RUNWAY',
    'Rapid Exit Taxiway': 'TAXIWAY',
    'Approach Lighting': 'APPROACH AND LANDING', 'PAPI': 'APPROACH AND LANDING',
    'VASIS': 'APPROACH AND LANDING', 'ILS': 'APPROACH AND LANDING',
    'Localizer': 'APPROACH AND LANDING', 'Glide Path': 'APPROACH AND LANDING',
    'Instrument Approach': 'APPROACH AND LANDING',
    'Approach Procedures': 'APPROACH AND LANDING', 'Landing': 'APPROACH AND LANDING',
    'MLS': 'APPROACH AND LANDING', 'Approach Lights': 'APPROACH AND LANDING',
    'SID': 'DEPARTURE PROCEDURES',
    'Standard Instrument Departure': 'DEPARTURE PROCEDURES',
    'Departure Procedures': 'DEPARTURE PROCEDURES',
    'VOR': 'NAVIGATION AIDS', 'DME': 'NAVIGATION AIDS', 'NDB': 'NAVIGATION AIDS',
    'TACAN': 'NAVIGATION AIDS', 'VORTAC': 'NAVIGATION AIDS',
    'Navigation Aid': 'NAVIGATION AIDS', 'GNSS': 'NAVIGATION AIDS',
    'Communication': 'COMMUNICATION', 'Radio': 'COMMUNICATION',
    'SELCAL': 'COMMUNICATION', 'Radar': 'COMMUNICATION',
    'Runway Lights': 'RUNWAY', 'Taxiway Lights': 'TAXIWAY',
    'Lighting': 'GENERAL', 'Services': 'GENERAL', 'Fuel': 'GENERAL',
    'De-icing': 'GENERAL', 'Fire and Rescue': 'GENERAL', 'Customs': 'GENERAL',
    'Obstacle': 'GENERAL', 'Warning': 'WARNING', 'Other': 'GENERAL',
    'Airport': 'GENERAL',
}

_QCODE_CAT_MAP = {
    'approach': 'APPROACH AND LANDING', 'landing': 'APPROACH AND LANDING',
    'ils': 'APPROACH AND LANDING', 'runway': 'RUNWAY', 'apron': 'APRON',
    'taxiway': 'TAXIWAY', 'navigation aid': 'NAVIGATION AIDS',
    'vor': 'NAVIGATION AIDS', 'dme': 'NAVIGATION AIDS', 'ndb': 'NAVIGATION AIDS',
    'communication': 'COMMUNICATION', 'radio': 'COMMUNICATION',
    'sid': 'DEPARTURE PROCEDURES', 'departure proc': 'DEPARTURE PROCEDURES',
    'obstacle': 'GENERAL', 'warning': 'WARNING', 'services': 'GENERAL',
    'other': 'GENERAL', 'airport': 'GENERAL',
}

_KEYWORD_CAT = [
    (['SID ', 'DEPARTURE (RNAV)', 'ODP ', 'OBSTACLE DEPARTURE',
      'STANDARD INSTRUMENT DEPARTURE'],                    'DEPARTURE PROCEDURES'),
    (['ILS ', 'LOC ', 'IAP ', 'APPROACH', 'PAPI',
      'ALS ', 'RVR ', ' LANDING'],                        'APPROACH AND LANDING'),
    (['RWY ', 'RUNWAY '],                                  'RUNWAY'),
    (['TWY ', 'TAXI', 'TAXIWAY'],                          'TAXIWAY'),
    (['APRON', ' RAMP', 'STAND ', 'GATE '],                'APRON'),
    (['COM ', 'COMM ', 'RADIO', 'FREQ '],                  'COMMUNICATION'),
    (['VORTAC', 'VOR ', 'DME ', 'NDB ', 'NAVAID', 'TACAN', ' ILS '], 'NAVIGATION AIDS'),
]

# Role &#8594; category display order
_NOTAM_CAT_ORDER = {
    'DEPARTURE': [
        'GENERAL', 'RUNWAY', 'TAXIWAY', 'APRON',
        'DEPARTURE PROCEDURES', 'COMMUNICATION', 'NAVIGATION AIDS',
        'APPROACH AND LANDING', 'WARNING', 'OTHER',
    ],
    'DESTINATION': [
        'GENERAL', 'APPROACH AND LANDING', 'RUNWAY', 'NAVIGATION AIDS',
        'TAXIWAY', 'APRON', 'COMMUNICATION', 'DEPARTURE PROCEDURES',
        'WARNING', 'OTHER',
    ],
    'DEFAULT': [
        'GENERAL', 'APPROACH AND LANDING', 'RUNWAY', 'NAVIGATION AIDS',
        'TAXIWAY', 'APRON', 'COMMUNICATION', 'DEPARTURE PROCEDURES',
        'WARNING', 'OTHER',
    ],
}


def _notam_qcode_to_cat(qcode_subj, qcode_cat="", text="", valid_cats=None):
    """4-level NOTAM category mapping matching MASTERLOG logic."""
    if valid_cats is None:
        valid_cats = _NOTAM_CAT_ORDER['DEFAULT']

    # 1. Exact subject lookup
    exact = _QCODE_SUBJECT_EXACT.get(qcode_subj)
    if exact and exact in valid_cats:
        return exact
    # 2. Exact category lookup
    exact_cat = _QCODE_SUBJECT_EXACT.get(qcode_cat)
    if exact_cat and exact_cat in valid_cats:
        return exact_cat
    # 3. Substring fallback on both fields
    for raw in (qcode_subj, qcode_cat):
        key = raw.casefold()
        for pat, cat in _QCODE_CAT_MAP.items():
            if pat in key and cat in valid_cats:
                return cat
    # 4. Keyword scan of body text
    text_upper = " " + text.upper() + " "
    for keywords, cat in _KEYWORD_CAT:
        if cat in valid_cats and any(kw in text_upper for kw in keywords):
            return cat

    return 'GENERAL' if 'GENERAL' in valid_cats else (valid_cats[0] if valid_cats else 'GENERAL')


def _format_notam_date(dt):
    """Format datetime as DDMMMhhmm or PERM."""
    if dt is None:
        return 'PERM'
    return dt.strftime('%d%b %H:%Mz').upper()


def _render_notam_html(n):
    """Render a single <notam> XML element to HTML."""
    import html as _html
    nid      = (n.findtext('notam_id')             or '---').strip()
    text     = (n.findtext('notam_text')            or '').strip()
    date_eff = (n.findtext('date_effective')        or '').strip()
    date_exp = (n.findtext('date_expire')           or '').strip()
    date_cre = (n.findtext('date_created') or n.findtext('date_modified') or '').strip()
    loc_icao = (n.findtext('location_icao')         or '').strip()
    is_est   = n.find('date_expire_is_estimated') is not None

    eff_dt = _parse_notam_iso_date(date_eff)
    exp_dt = None if is_est else _parse_notam_iso_date(date_exp)
    expired = _notam_is_expired(exp_dt) if exp_dt else False

    eff_str = _format_notam_date(eff_dt) if eff_dt else '---'
    exp_str = ('UFN' if is_est else _format_notam_date(exp_dt)) if date_exp else 'PERM'

    exp_class = ' expired' if expired else ''
    safe_nid  = _html.escape(nid)
    safe_text = _html.escape(text)
    meta = f"{loc_icao}  " if loc_icao else ""
    meta += f"EFF {eff_str}  EXP {exp_str}"

    h  = f"<div class='notam-entry{exp_class}' data-nid='{safe_nid}'>\n"
    h += f"  <div class='notam-entry-hdr'>"
    h += f"    <span class='notam-id'>{safe_nid}</span>"
    h += f"    <span class='notam-meta'>{_html.escape(meta)}</span>"
    h += f"    <button class='notam-pin-btn' onclick='togglePin(\"{safe_nid}\", this.closest(\".notam-entry\"))' title='Pin NOTAM'>&#9675;</button>"
    h += f"  </div>\n"
    h += f"  <div class='notam-body'>{safe_text}</div>\n"
    h += "</div>\n"
    return h, expired


def _render_airport_notams_html(section, role, xml_root):
    """Render all NOTAMs for one airport section inside a collapsible sub-header."""
    import html as _html
    if section is None:
        return ''

    notams_list = (section.find('notams') or section).findall('.//notam')
    if not notams_list:
        return ''

    # Get airport info
    icao = ''
    for f in ('icao_code', 'icao', 'orig_icao', 'dest_icao'):
        icao = (section.findtext(f) or '').strip()
        if icao:
            break
    iata = (section.findtext('iata_code') or section.findtext('orig_iata') or
            section.findtext('dest_iata') or '').strip()
    name = (section.findtext('name') or section.findtext('orig_name') or
            section.findtext('dest_name') or '').strip()

    # Role-specific category order matching MASTERLOG
    role_key = 'DEPARTURE' if 'DEPART' in role.upper() else \
               'DESTINATION' if 'DEST' in role.upper() else 'DEFAULT'
    CATEGORY_ORDER = _NOTAM_CAT_ORDER[role_key]
    categorized = {cat: [] for cat in CATEGORY_ORDER}

    for n in notams_list:
        qcode_subj = (n.findtext('notam_qcode_subject')  or '').strip()
        qcode_cat  = (n.findtext('notam_qcode_category') or '').strip()
        text       = (n.findtext('notam_text')           or '').strip()
        cat = _notam_qcode_to_cat(qcode_subj, qcode_cat, text, CATEGORY_ORDER)
        if cat not in categorized:
            cat = 'GENERAL'
        entry_html, expired = _render_notam_html(n)
        categorized[cat].append((entry_html, expired))

    airport_label = icao
    if iata:
        airport_label += f" / {iata}"
    if name:
        airport_label += f" &mdash; {_html.escape(name)}"

    # Build category content first so we can bail if empty
    content = ''
    for cat in CATEGORY_ORDER:
        items = categorized.get(cat, [])
        if not items:
            continue
        active_items  = [(h, e) for h, e in items if not e]
        expired_items = [(h, e) for h, e in items if e]
        if not active_items and not expired_items:
            continue
        content += f"<div class='notam-category-bar'>{cat}</div>\n"
        for entry_html, _ in active_items:
            content += entry_html
        if expired_items:
            content += "<div class='notam-expired-div'>&mdash; EXPIRED &mdash;</div>\n"
            for entry_html, _ in expired_items:
                content += entry_html

    if not content:
        return ''

    # Unique ID for this sub-section
    sub_id = f"notam-sub-{icao.lower().replace('/', '-')}-{role.lower().replace(' ', '-')}"
    count = len(notams_list)

    out  = f"<div class='notam-sub-section'>\n"
    out += (f"  <div class='notam-sub-header' onclick=\"toggleNotamSub('{sub_id}')\">\n"
            f"    <span class='notam-airport-role-badge'>{_html.escape(role)}</span>\n"
            f"    <span class='notam-sub-title'>{airport_label}</span>\n"
            f"    <span class='notam-sub-count'>{count}</span>\n"
            f"    <span class='notam-sub-arrow' id='{sub_id}-arrow'>&#9660;</span>\n"
            f"  </div>\n"
            f"  <div id='{sub_id}' class='notam-sub-body'>\n"
            + content +
            f"  </div>\n"
            f"</div>\n")
    return out



def _build_weather_html(root):
    """
    Build the Weather HTML section, mirroring MASTERLOG weather layout:
      - One collapsible sub-section per airport (DEPARTURE, DESTINATION,
        TKOF ALTN, ENRTE ALTN, ALTERNATE 1&hellip;) with TAF / METAR / ATIS blocks
      - One collapsible sub-section per FIR in navlog order for SIGMETs
    """
    import html as _h
    from collections import OrderedDict

    def _wx_sub(sub_id, role_badge, title, body_html):
        """Wrap weather content in the same collapsible sub-section style as NOTAMs."""
        out  = f"<div class='notam-sub-section'>\n"
        out += (f"  <div class='notam-sub-header' onclick=\"toggleNotamSub('{sub_id}')\">"
                f"    <span class='notam-airport-role-badge'>{_h.escape(role_badge)}</span>"
                f"    <span class='notam-sub-title'>{_h.escape(title)}</span>"
                f"    <span class='notam-sub-arrow' id='{sub_id}-arrow'>&#9660;</span>"
                f"  </div>\n"
                f"  <div id='{sub_id}' class='notam-sub-body'>\n"
                + body_html +
                f"  </div>\n"
                f"</div>\n")
        return out

    def _wx_cat(label):
        return f"<div class='notam-category-bar'>{_h.escape(label)}</div>\n"

    def _wx_text(text):
        if not text or not text.strip():
            return "<div class='wx-nil'>NIL</div>\n"
        safe = _h.escape(text.strip())
        return f"<div class='wx-text'><pre style='margin:0;white-space:pre-wrap;word-break:break-all;font-family:monospace;font-size:12px;'>{safe}</pre></div>\n"

    def _from_node(node):
        """Extract wx data from an XML airport node."""
        if node is None:
            return None
        icao  = (node.findtext('icao_code') or node.findtext('orig_icao') or
                 node.findtext('dest_icao') or '').strip().upper()
        if not icao:
            return None
        iata  = (node.findtext('iata_code') or node.findtext('orig_iata') or
                 node.findtext('dest_iata') or '').strip().upper()
        name  = (node.findtext('name') or node.findtext('orig_name') or
                 node.findtext('dest_name') or '').strip()
        metar = (node.findtext('metar') or '').strip()
        taf   = (node.findtext('taf')   or '').strip()
        # Best ATIS: prefer real-world network; merge DEP + ARR
        NETWORK_PRIORITY = {'real-world': 0, 'pilotedge': 1, 'vatsim': 2, 'ivao': 3}
        best = {}
        for atis_el in node.findall('atis'):
            network = (atis_el.findtext('network') or '').strip().lower()
            raw_type = (atis_el.findtext('type') or atis_el.findtext('atis_type') or 'DEP').strip().upper()
            atype = 'ARR' if 'ARR' in raw_type else 'DEP'
            msg   = (atis_el.findtext('message') or atis_el.findtext('text') or atis_el.text or '').strip()
            if not msg:
                continue
            pri = NETWORK_PRIORITY.get(network, 99)
            if atype not in best or pri < best[atype][0]:
                best[atype] = (pri, msg)
        dep_atis = best.get('DEP', (0, ''))[1]
        arr_atis = best.get('ARR', (0, ''))[1]
        if not dep_atis and not arr_atis:
            flat = (node.findtext('atis') or '').strip()
            if flat:
                dep_atis = flat
        return {'icao': icao, 'iata': iata, 'name': name,
                'metar': metar, 'taf': taf,
                'dep_atis': dep_atis, 'arr_atis': arr_atis}

    def _render_station(wx, role, idx=0):
        if not wx:
            return ''
        has_wx = any(wx.get(k, '').strip() for k in ('metar', 'taf', 'dep_atis', 'arr_atis'))
        if not has_wx:
            return ''
        icao  = wx['icao']
        title = icao
        if wx['iata']:
            title += f" / {wx['iata']}"
        if wx['name']:
            title += f" &mdash; {wx['name']}"
        sub_id = f"wx-sub-{icao.lower()}-{role.lower().replace(' ','-')}-{idx}"
        body = ''
        body += _wx_cat('TAF')
        body += _wx_text(wx['taf'])
        body += _wx_cat('METAR')
        body += _wx_text(wx['metar'])
        atis_combined = ''
        if wx['dep_atis']:
            atis_combined += wx['dep_atis'].strip()
        if wx['arr_atis'] and wx['arr_atis'] != wx['dep_atis']:
            atis_combined += ('\n' if atis_combined else '') + wx['arr_atis'].strip()
        if atis_combined:
            body += _wx_cat('ATIS')
            body += _wx_text(atis_combined)
        return _wx_sub(sub_id, role, title, body)

    out = ''

    # Airport stations in operational order
    STATIONS = [
        (root.find('origin'),       'DEPARTURE'),
        (root.find('destination'),  'DESTINATION'),
        (root.find('takeoff_altn'), 'TKOF ALTN'),
        (root.find('enroute_altn'), 'ENRTE ALTN'),
    ]
    for node, role in STATIONS:
        out += _render_station(_from_node(node), role)

    for i, alt in enumerate(root.findall('alternate'), 1):
        out += _render_station(_from_node(alt), f'ALTERNATE {i}', i)

    # SIGMETs &mdash; navlog FIR order (mirrors MASTERLOG)
    navlog_firs = OrderedDict()
    for fix in root.findall('navlog/fix'):
        fcode = (fix.findtext('fir') or '').strip().upper()
        if fcode and fcode not in navlog_firs:
            navlog_firs[fcode] = ''

    sigmet_map = {}
    for sig in root.findall('weather/sigmets/sigmet') or root.findall('sigmets/sigmet'):
        fcode = (sig.findtext('fir') or '').strip().upper()
        fname = (sig.findtext('fir_name') or '').strip()
        if not fcode:
            continue
        sigmet_map.setdefault(fcode, []).append(sig)
        if fname:
            navlog_firs[fcode] = fname
        if fcode not in navlog_firs:
            navlog_firs[fcode] = fname

    for fcode, fname in navlog_firs.items():
        display = fname or f"{fcode} FIR/UIR"
        sub_id  = f"wx-sub-{fcode.lower()}-sigmet"
        body = _wx_cat('SIGMET')
        sigs = sigmet_map.get(fcode, [])
        if sigs:
            for sig in sigs:
                sig_id  = (sig.findtext('id')     or '').strip()
                hazard  = (sig.findtext('hazard') or '').strip()
                text    = (sig.findtext('text')   or '').strip()
                hdr     = f"SIGMET {sig_id} ({hazard})" if sig_id else "SIGMET"
                body   += _wx_text(f"{hdr}\n{text}" if text else hdr)
        else:
            body += "<div class='wx-nil'>NIL &mdash; NO ACTIVE SIGMET FOR THIS FIR</div>\n"
        out += _wx_sub(sub_id, 'SIGMET', f"{fcode} &mdash; {display}", body)

    return out


def _build_notams_html(root):
    """Build the full NOTAM HTML block from the SimBrief XML root."""
    out = ''

    # Track ICAOs rendered in airport sections so enroute FIRs can exclude them
    already_shown = set()

    def _track_icao(node, *fields):
        if node is None:
            return
        for f in fields:
            v = (node.findtext(f) or '').strip().upper()
            if v:
                already_shown.add(v)
                return

    # Departure
    orig = root.find('origin')
    _track_icao(orig, 'icao_code', 'icao', 'orig_icao')
    dep_html = _render_airport_notams_html(orig, 'DEPARTURE', root)
    if dep_html:
        out += dep_html

    # Destination
    dest = root.find('destination')
    _track_icao(dest, 'icao_code', 'icao', 'dest_icao')
    arr_html = _render_airport_notams_html(dest, 'DESTINATION', root)
    if arr_html:
        out += arr_html

    # Takeoff alternate
    toa = root.find('takeoff_altn')
    _track_icao(toa, 'icao_code', 'icao')
    toa_html = _render_airport_notams_html(toa, 'TKOF ALTN', root)
    if toa_html:
        out += toa_html

    # Enroute alternate
    era = root.find('enroute_altn')
    _track_icao(era, 'icao_code', 'icao')
    era_html = _render_airport_notams_html(era, 'ENRTE ALTN', root)
    if era_html:
        out += era_html

    # Destination alternates
    for i, alt in enumerate(root.findall('alternate'), 1):
        _track_icao(alt, 'icao_code', 'icao')
        alt_html = _render_airport_notams_html(alt, f'ALTERNATE {i}', root)
        if alt_html:
            out += alt_html

    # Enroute NOTAMs (top-level <notams> block) &mdash; ordered by navlog FIR sequence
    enrt_root = root.find('notams')
    if enrt_root is not None:
        import html as _html_mod
        from collections import OrderedDict as _OD

        # Build navlog-ordered FIR list (mirrors MASTERLOG get_enroute_notams)
        _navlog_fir_order = []
        _seen_firs = set()
        for _fix in root.findall('navlog/fix'):
            _fcode = (_fix.findtext('fir') or '').strip().upper()
            if _fcode and _fcode not in _seen_firs:
                _navlog_fir_order.append(_fcode)
                _seen_firs.add(_fcode)

        recs = enrt_root.findall('notamdrec')
        if recs:
            by_fir = _OD()
            for rec in recs:
                notam_text = (rec.findtext('notam_text') or '').strip()
                if not notam_text:
                    continue
                fir = (rec.findtext('icao_id') or rec.findtext('fir_id') or 'ENRT').strip().upper()
                # Skip ICAOs already covered by a dedicated airport section
                if fir in already_shown:
                    continue
                if fir not in by_fir:
                    by_fir[fir] = []
                by_fir[fir].append(rec)

            # Render in navlog FIR order, then any remainder not seen in navlog
            fir_order = [k for k in _navlog_fir_order if k in by_fir]
            fir_order += [k for k in by_fir if k not in fir_order]

            if fir_order:
                out += "<div class='notam-airport-header'>\n"
                out += "  <div class='notam-airport-title'>ENROUTE NOTAMS</div>\n"
                out += "</div>\n"
                import xml.etree.ElementTree as _ET
                for fir in fir_order:
                    # Build NOTAM entries for this FIR
                    fir_content = ''
                    for rec in by_fir[fir]:
                        n_el = _ET.Element('notam')
                        for tag, src in [
                            ('notam_id', 'notam_id'), ('notam_text', 'notam_text'),
                            ('date_effective', 'date_effective'), ('date_expire', 'date_expire'),
                            ('date_created', 'date_created'), ('location_icao', 'icao_id'),
                        ]:
                            val = rec.findtext(src) or ''
                            sub = _ET.SubElement(n_el, tag)
                            sub.text = val
                        if rec.find('date_expire_is_estimated') is not None:
                            _ET.SubElement(n_el, 'date_expire_is_estimated')
                        entry_html, _ = _render_notam_html(n_el)
                        fir_content += entry_html

                    # Wrap in the same collapsible sub-section used by airport sections
                    sub_id = f"notam-sub-{fir.lower().replace('/', '-')}-enroute"
                    count = len(by_fir[fir])
                    out += f"<div class='notam-sub-section'>\n"
                    out += (f"  <div class='notam-sub-header' onclick=\"toggleNotamSub('{sub_id}')\">\n"
                            f"    <span class='notam-airport-role-badge'>ENROUTE</span>\n"
                            f"    <span class='notam-sub-title'>{_html_mod.escape(fir)}</span>\n"
                            f"    <span class='notam-sub-count'>{count}</span>\n"
                            f"    <span class='notam-sub-arrow' id='{sub_id}-arrow'>&#9660;</span>\n"
                            f"  </div>\n"
                            f"  <div id='{sub_id}' class='notam-sub-body'>\n"
                            + fir_content +
                            f"  </div>\n"
                            f"</div>\n")

    return out

ARCHIVE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aviobook_archive")
LAUNCHER_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aviobook_launcher.html")


def _build_launcher_html(archive_folder):
    """Scan archive folder and build a launcher HTML listing all past flights."""
    import glob, json as _json

    entries = []
    pattern = os.path.join(archive_folder, "*.html")
    for fpath in sorted(glob.glob(pattern), reverse=True):
        fname = os.path.basename(fpath)
        # Filename format: ORIG-DEST_FLT_YYYYMMDD_HHMM.html
        meta_path = fpath.replace(".html", ".json")
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as mf:
                    meta = _json.load(mf)
            except Exception:
                pass
        orig      = meta.get("orig", "???")
        dest      = meta.get("dest", "???")
        flt       = meta.get("flight", "")
        date_str  = meta.get("date", "")
        time_str  = meta.get("time", "")
        entries.append({
            "fname": fname, "fpath": fpath,
            "orig": orig, "dest": dest,
            "flight": flt, "date": date_str, "time": time_str
        })

    rows = ""
    for e in entries:
        label   = f"{e['flight']}  {e['orig']} &#8594; {e['dest']}" if e['flight'] else f"{e['orig']} &#8594; {e['dest']}"
        sub     = f"{e['date']}  {e['time']} UTC" if e['date'] else e['fname']
        safe_fname = _html_escape.escape(e['fname'])
        rows += (
            f"<a href='{safe_fname}' class='fl-row'>"
            f"<div class='fl-route'>{_html_escape.escape(label)}</div>"
            f"<div class='fl-meta'>{_html_escape.escape(sub)}</div>"
            f"<div class='fl-chev'>&#8250;</div>"
            f"</a>\n"
        )

    if not rows:
        rows = "<div style='padding:32px 20px;text-align:center;color:#4a7a96;font-size:14px;'>No archived flights yet.</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Aviobook &mdash; Flights</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{background:#0d3550;overscroll-behavior-y:none;}}
body{{background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);
  min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;
  overscroll-behavior-y:none;}}
.header{{background:rgba(0,0,0,0.3);padding:18px 20px 14px;
  border-bottom:1px solid rgba(90,174,239,0.18);display:flex;align-items:center;gap:12px;}}
.header-logo{{font-size:20px;font-weight:700;color:#7ad8fd;letter-spacing:1px;}}
.header-sub{{font-size:11px;color:#4a7a96;letter-spacing:.5px;margin-top:2px;}}
.new-btn{{margin-left:auto;background:linear-gradient(90deg,#1a6a9a,#1e7db8);border:none;
  border-radius:6px;color:#fff;font-size:12px;font-weight:700;letter-spacing:.5px;
  padding:9px 16px;cursor:pointer;text-transform:uppercase;text-decoration:none;display:inline-block;}}
.section-title{{padding:18px 20px 8px;font-size:11px;color:#4a7a96;letter-spacing:1px;text-transform:uppercase;}}
.fl-row{{display:flex;align-items:center;padding:14px 20px;
  border-bottom:1px solid rgba(90,174,239,0.1);text-decoration:none;cursor:pointer;gap:10px;
  transition:background .15s;}}
.fl-row:active{{background:rgba(90,174,239,0.08);}}
.fl-route{{flex:1;font-size:15px;font-weight:600;color:#e8f6ff;letter-spacing:.2px;}}
.fl-meta{{font-size:11px;color:#4a7a96;white-space:nowrap;}}
.fl-chev{{font-size:22px;color:#2a6a8b;line-height:1;}}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="header-logo">AVIOBOOK</div>
    <div class="header-sub">FLIGHT ARCHIVE</div>
  </div>
  <a href="aviobook_flightplan.html" class="new-btn">&#9654; Current Flight</a>
</div>
<div class="section-title">Past Flights</div>
{rows}
</body>
</html>
"""


def main():
    import os, json as _json
    from datetime import datetime, timezone

    import sys as _sys
    username = os.environ.get("SIMBRIEF_USERNAME") or (len(_sys.argv) > 1 and _sys.argv[1]) or ""
    if not username:
        print("Usage: python3 Aviobook.py <simbrief_username>", file=_sys.stderr)
        print("   or: SIMBRIEF_USERNAME=yourname python3 Aviobook.py", file=_sys.stderr)
        _sys.exit(1)
    try:
        xml_data = fetch_xml_from_api(username)
    except RuntimeError as e:
        print(f"\nError: {e}", file=_sys.stderr)
        print(f"Tip: Make sure '{username}' matches your SimBrief Pilot ID (alphanumeric alias) or numeric User ID.", file=_sys.stderr)
        _sys.exit(1)
    data     = parse_simbrief_xml(xml_data)
    html     = generate_aviobook_html(data)

    # &#9472;&#9472; Save current flight &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    current_path = "aviobook_flightplan.html"
    with open(current_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Flight plan saved as '{current_path}'.")

    # &#9472;&#9472; Archive &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    os.makedirs(ARCHIVE_FOLDER, exist_ok=True)

    g    = data.get("general", {})
    a    = data.get("airports", {})
    orig = a.get("origin", {}).get("icao", "???")
    dest = a.get("destination", {}).get("icao", "???")
    flt  = (g.get("icao_airline", "") + g.get("flight_number", "")).strip()

    # Use scheduled departure timestamp for the archive filename
    try:
        ts_unix  = int(data["times"].get("sched_off_ts") or 0)
        dt_utc   = datetime.fromtimestamp(ts_unix, tz=timezone.utc) if ts_unix else datetime.now(timezone.utc)
    except Exception:
        dt_utc = datetime.now(timezone.utc)

    date_str  = dt_utc.strftime("%Y%m%d")
    time_str  = dt_utc.strftime("%H%M")
    safe_flt  = flt.replace("/", "").replace(" ", "") or "FLT"
    arc_stem  = f"{orig}-{dest}_{safe_flt}_{date_str}_{time_str}"
    arc_html  = os.path.join(ARCHIVE_FOLDER, arc_stem + ".html")
    arc_meta  = os.path.join(ARCHIVE_FOLDER, arc_stem + ".json")

    # Only write if this exact flight isn't already archived
    if not os.path.exists(arc_html):
        with open(arc_html, "w", encoding="utf-8") as f:
            f.write(html)
        with open(arc_meta, "w", encoding="utf-8") as f:
            _json.dump({
                "orig": orig, "dest": dest, "flight": flt,
                "date": dt_utc.strftime("%d %b %Y"),
                "time": dt_utc.strftime("%H:%M"),
            }, f)
        print(f"Archived as '{arc_html}'.")

    # &#9472;&#9472; Rebuild launcher &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    launcher_html = _build_launcher_html(ARCHIVE_FOLDER)
    with open(LAUNCHER_FILE, "w", encoding="utf-8") as f:
        f.write(launcher_html)
    print(f"Launcher updated: '{LAUNCHER_FILE}'.")


# &#9472;&#9472; Default release folder (override via env or server config) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
DEFAULT_RELEASE_FOLDER = os.environ.get(
    "AVIOBOOK_RELEASE_FOLDER",
    os.path.join(os.path.expanduser("~"), "Dropbox", "Apps", "ForeFlight", "RELEASES")
)

def scan_release_folder(orig_icao, dest_icao, flight_number, folder=None):
    """
    Scan folder for PDFs matching this flight. Returns list of dicts:
      [{'name': str, 'score': int, 'data_uri': str}, ...]
    sorted best-match first.  data_uri is a base64 PDF data URI ready for <iframe src=>.
    Returns [] if folder missing or no PDFs found.
    """
    import os, base64

    folder = folder or DEFAULT_RELEASE_FOLDER
    if not os.path.isdir(folder):
        return []

    def score_and_type(name):
        """
        Parse filenames like:  KSJCKLAX638307MAR-RLS.pdf
                                KSJCKLAX638307MAR-WB.pdf
        Format: {ORIG}{DEST}{FLTNUM}{DATE}-{TYPE}.pdf
        Returns (score, doc_type) where doc_type is 'RLS', 'WB', or ''
        """
        stem = name.upper().replace('.PDF', '')
        # Detect suffix type
        doc_type = ''
        for suffix in ('-RLS', '-WB', '-OFP', '-RELEASE', '-WEIGHTBALANCE'):
            if stem.endswith(suffix):
                doc_type = suffix.lstrip('-')
                stem = stem[:-len(suffix)]
                break

        # Strip everything after last digit run (date portion like 07MAR, 07MAR2026)
        # leaving us with {ORIG}{DEST}{FLTNUM}
        core = stem.replace('-','').replace('_','').replace(' ','')

        pair = (orig_icao + dest_icao).upper()
        flt  = flight_number.upper().replace(' ','')

        s = 0
        if pair in core:                s += 100
        if flt and flt in core:         s += 60
        elif orig_icao.upper() in core: s += 20
        if dest_icao.upper() in core:   s += 20
        # Bonus for known suffix types
        if doc_type in ('RLS', 'WB'):   s += 10
        return s, doc_type

    results = []
    try:
        for fname in os.listdir(folder):
            if not fname.upper().endswith('.PDF'):
                continue
            s, doc_type = score_and_type(fname)
            if s == 0:
                continue
            fpath = os.path.join(folder, fname)
            # Defer base64 encoding to render time so generation is fast
            results.append({'name': fname, 'score': s,
                             'doc_type': doc_type, 'fpath': fpath, 'data_uri': None})
    except Exception:
        pass

    results.sort(key=lambda x: x['score'], reverse=True)

    # Encode only top 2 PDFs (most likely to be viewed) to keep file size manageable
    import base64 as _b64
    for r in results[:2]:
        try:
            with open(r['fpath'], 'rb') as fh:
                r['data_uri'] = f"data:application/pdf;base64,{_b64.b64encode(fh.read()).decode('ascii')}"
        except Exception:
            r['data_uri'] = ''

    return results

def generate_aviobook_html(data, pilot_name="", release_folder=None):
    # Resolve captain name: user-supplied pilot_name wins over SimBrief crew/cpt
    c_raw = data.get('crew', {})
    _captain_name = (pilot_name or c_raw.get('cpt') or '').strip().upper()

    def time_row(label, sched, est):
        return f"<div class='data-row'><span class='label'>{label}:</span> {sched[0]}/{sched[1]} &mdash; {est[0]}/{est[1]}</div>"

    def fuel_row(label, lbs, seconds):
        if not lbs or int(lbs) == 0:
            return ""
        sec_int = int(seconds) if seconds is not None and str(seconds) != '' else 0
        hhmm = f"{sec_int//3600:02}{(sec_int%3600)//60:02}" if sec_int else "----"
        return f"<div class='data-row'><span class='label'>{label}:</span> {lbs} lbs / {hhmm}</div>"

    def format_navlog_time(seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:02d}:{minutes:02d}"

    # Extract and format enroute times for later use
    sched_time_enroute = sec_to_hhmm(data['times'].get('sched_time_enroute', '0'))
    est_time_enroute = sec_to_hhmm(data['times'].get('est_time_enroute', '0'))

    # Also extract any other times you need (sched_off, est_out, etc) here similarly
    sched_off_display = data['times']['sched_off'][0] if isinstance(data['times']['sched_off'], (list, tuple)) else data['times']['sched_off']
    sched_off_local_display = data['times']['sched_off'][1] if isinstance(data['times']['sched_off'], (list, tuple)) else ""

    est_out_display = data['times']['est_out'][0] if isinstance(data['times']['est_out'], (list, tuple)) else data['times']['est_out']
    est_out_local_display = data['times']['est_out'][1] if isinstance(data['times']['est_out'], (list, tuple)) else ""

    sched_in_display = data['times']['sched_in'][0] if isinstance(data['times']['sched_in'], (list, tuple)) else data['times']['sched_in']
    sched_in_local_display = data['times']['sched_in'][1] if isinstance(data['times']['sched_in'], (list, tuple)) else ""

    est_in_display = data['times']['est_in'][0] if isinstance(data['times']['est_in'], (list, tuple)) else data['times']['est_in']
    est_in_local_display = data['times']['est_in'][1] if isinstance(data['times']['est_in'], (list, tuple)) else ""



    css = """
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(160deg, #13405a 0%, #1a4a61 50%, #163d55 100%);
            background-attachment: fixed;
            color: #eaf6ff;
            padding: 0;
            margin: 0;
            padding-bottom: 80px;
            font-size: 14px;
        }
        /* iOS Safari: prevent tap delay and ensure keyboard opens on all inputs */
        input, textarea, select {
            touch-action: manipulation;
            -webkit-user-select: text;
            user-select: text;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 0 0 10px 0;
        }
        /* &#9472;&#9472; TOP STATUS BAR &#9472;&#9472; */
        .top-bar {
            background: #0b1f30;
            border-bottom: 1px solid #1a3a50;
            position: sticky;
            top: 0;
            z-index: 700;
            padding-top: env(safe-area-inset-top, 0px);
        }
        .top-bar-inner {
            max-width: 900px;
            margin: 0 auto;
            padding: 10px 16px 0 16px;
        }
        .top-bar-time {
            font-size: 22px;
            font-weight: 700;
            color: #4de8ff;
            letter-spacing: 0.3px;
            white-space: nowrap;
        }
        .top-bar-flt {
            font-size: 14px;
            font-weight: 700;
            color: #eaf6ff;
            letter-spacing: 0.2px;
        }
        .top-bar-reg {
            font-size: 13px;
            font-weight: 400;
            color: #7ab8d4;
        }
        /* Row 2: route line */
        .top-bar-row2 {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            margin-top: 2px;
            margin-bottom: 8px;
        }
        .top-bar-row2 .icao {
            color: #8ac8e0;
            font-weight: 600;
            font-size: 13px;
        }
        .top-bar-row2 .times { color: #5a8fa8; font-size: 13px; }
        .top-bar-row2 .arrow { color: #5a9ab5; font-size: 15px; }
        /* Right icon cluster */
        .top-bar-icons {
            display: flex;
            align-items: center;
            gap: 16px;
            flex-shrink: 0;
            padding-bottom: 8px;
        }
        .top-bar-icon-btn {
            background: none;
            border: none;
            cursor: pointer;
            color: #7ab8d4;
            font-size: 19px;
            padding: 0;
            position: relative;
            display: flex;
            align-items: center;
            line-height: 1;
        }
        .top-bar-icon-btn:hover { color: #eaf6ff; }
        .top-bar-icon-btn .badge {
            position: absolute;
            top: -6px;
            right: -8px;
            background: #d4a017;
            color: #0b1f30;
            border-radius: 50%;
            font-size: 9px;
            font-weight: 800;
            width: 15px;
            height: 15px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        /* Tab bar */
        .tab-bar {
            display: flex;
            overflow-x: auto;
            scrollbar-width: none;
            margin: 0 -16px;
            padding: 0 16px;
        }
        .tab-bar::-webkit-scrollbar { display: none; }
        .tab {
            padding: 9px 20px;
            text-transform: uppercase;
            font-size: 12px;
            font-weight: 500;
            white-space: nowrap;
            color: #4e7a96;
            letter-spacing: 0.6px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
        }
        .tab:hover { color: #9ac8e0; }
        .tab.active {
            border-bottom: 2px solid #4de8ff;
            color: #ffffff;
            font-weight: 600;
        }
        /* &#9472;&#9472; CONTENT AREA &#9472;&#9472; */
        .content {
            padding: 12px 12px 0 12px;
        }
        /* &#9472;&#9472; OVERLAY WIDTH CONSTRAINT &#9472;&#9472; */
        /* All fixed overlays fill the viewport but constrain content to container width */
        .overlay-inner {
            max-width: 900px;
            margin: 0 auto;
            padding: 0 0 80px 0;
            min-height: 100%;
        }
        /* &#9472;&#9472; SECTIONS &#9472;&#9472; */
        .section {
            background: linear-gradient(160deg, #1a4a61 0%, #21546D 60%, #1c4a60 100%);
            margin-bottom: 10px;
            border-radius: 8px;
            border: 1px solid rgba(90,174,239,0.15);
            box-shadow: 0 2px 12px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .section-header {
            display: flex;
            align-items: center;
            padding: 14px 16px 12px 16px;
            font-size: 16px;
            font-weight: 600;
            color: #d8f0ff;
            cursor: pointer;
            user-select: none;
            gap: 10px;
            border-bottom: 1px solid rgba(90,174,239,0.15);
        }
        .section-header.collapsed {
            border-bottom: none;
        }
        .section-header:active { background: rgba(90,174,239,0.06); }
        .section-icon {
            color: #7ad8fd;
            font-size: 17px;
            width: 22px;
            text-align: center;
            flex-shrink: 0;
        }
        .section-header .collapse-arrow {
            margin-left: auto;
            color: #7ad8fd;
            font-size: 12px;
            transition: transform 0.2s ease;
        }
        .section-header.collapsed .collapse-arrow {
            transform: rotate(-90deg);
        }
        .section-body { padding: 10px 16px 12px 16px; }
        .section-body.collapsed { display: none; }

        /* &#9472;&#9472; SCHEDULE / FLIGHT ARC &#9472;&#9472; */
        .on-time-badge {
            display: inline-block;
            background: #1a7a3a;
            color: #fff;
            font-size: 12px;
            font-weight: 700;
            padding: 4px 14px;
            border-radius: 20px;
            letter-spacing: 0.5px;
            cursor: pointer;
            user-select: none;
            transition: opacity 0.15s;
        }
        .on-time-badge:active { opacity: 0.7; }
        .delayed-badge   { background: #7a4a00; color: #ffb84d; }
        .airborne-badge  { background: #0a3a6a; color: #7ad8fd; }
        .onblocks-badge  { background: #2a2a2a; color: #a0a0a0; }
        /* ICAO row above the swoop */
        .arc-icao-row {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 2px;
        }
        .arc-icao-left { text-align: left; }
        .arc-icao-right { text-align: right; }
        .arc-icao {
            font-size: 22px;
            font-weight: 700;
            color: #eaf6ff;
            line-height: 1;
            white-space: nowrap;
        }
        .arc-iata-gate {
            font-size: 11px;
            color: #6ab4d4;
            margin-top: 2px;
            line-height: 1.5;
        }
        /* Center column — full width */
        .arc-center {
            width: 100%;
            text-align: center;
        }
        .arc-flightnum {
            font-size: 20px;
            font-weight: 700;
            color: #eaf6ff;
            margin-bottom: 0;
        }
        .arc-ofp-badge {
            display: inline-block;
            border: 1.5px solid #7ad8fd;
            border-radius: 4px;
            color: #7ad8fd;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            letter-spacing: 0.3px;
        }
        /* SVG swoop */
        /* Swoop: fixed SVG hump flanked by scalable lines */
        .arc-swoop-row {
            display: flex;
            align-items: flex-end;
            width: 100%;
            margin: 4px 0;
        }
        .arc-swoop-line {
            flex: 1;
            height: 2.5px;
            background: #7ad8fd;
            border-radius: 2px;
            margin-bottom: 2px;
        }
        .arc-swoop-svg {
            flex-shrink: 0;
            width: 200px;
            height: 44px;
            display: block;
            overflow: visible;
        }
        /* Timeline sits directly under swoop dots */
        .arc-dot-labels {
            display: flex;
            width: 100%;
            margin-top: -6px;
        }
        .arc-dot-label {
            flex: 1;
            text-align: center;
            min-width: 0;
        }
        .arc-dot-label.gap { flex: 2; }
        .adl-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background: #7ad8fd;
            margin: 0 auto 3px auto;
        }
        .adl-lbl {
            font-size: 9px;
            color: #6ab4d4;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            white-space: nowrap;
        }
        .adl-val {
            font-size: 13px;
            font-weight: 700;
            color: #eaf6ff;
        }
        /* Meta: single centered column */
        .arc-meta {
            font-size: 11px;
            color: #6ab4d4;
            margin-top: 8px;
            text-align: center;
            line-height: 1.9;
        }
        .arc-meta strong { color: #d8f0ff; font-weight: 600; }
        /* &#9472;&#9472; DATA ROWS &#9472;&#9472; */
        .data-row {
            display: flex;
            justify-content: space-between;
            padding: 7px 0;
            border-bottom: 1px solid rgba(90,174,239,0.08);
            font-size: 13px;
            align-items: flex-start;
            gap: 10px;
        }
        .data-row:last-child { border-bottom: none; }
        .data-label {
            color: #6ab4d4;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            white-space: nowrap;
            min-width: 90px;
        }
        .data-value {
            color: #eaf6ff;
            font-size: 13px;
            text-align: right;
            flex: 1;
        }
        .route-box {
            background: linear-gradient(135deg, #1a4a61 0%, #21546d 100%);
            border: 1px solid rgba(150,210,245,0.20);
            border-radius: 5px;
            padding: 10px 12px;
            margin: 8px 0;
            font-family: 'SF Mono', 'Courier New', monospace;
            font-size: 12px;
            color: #eaf6ff;
            overflow-wrap: break-word;
            line-height: 1.5;
        }
        /* &#9472;&#9472; FUEL / WEIGHTS GRID &#9472;&#9472; */
        .fw-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
            gap: 8px;
        }
        .fw-item {
            background: linear-gradient(135deg, #1a4a61 0%, #21546d 100%);
            border: 1px solid rgba(150,210,245,0.18);
            border-radius: 5px;
            padding: 10px 12px;
        }
        .fw-label {
            font-size: 10px;
            color: #6ab4d4;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }
        .fw-value {
            font-size: 16px;
            font-weight: 700;
            color: #eaf6ff;
        }
        .fw-unit {
            font-size: 10px;
            color: #6ab4d4;
            font-weight: 400;
            margin-left: 3px;
        }
        /* &#9472;&#9472; ALTERNATE &#9472;&#9472; */
        .alt-block {
            background: linear-gradient(135deg, #1a4a61 0%, #21546d 100%);
            border: 1px solid rgba(150,210,245,0.18);
            border-radius: 5px;
            padding: 10px 12px;
            margin-bottom: 8px;
        }
        .alt-title {
            font-size: 13px;
            font-weight: 700;
            color: #7ad8fd;
            margin-bottom: 6px;
        }
        /* &#9472;&#9472; CREW &#9472;&#9472; */
        .crew-row {
            display: flex;
            gap: 12px;
            padding: 6px 0;
            border-bottom: 1px solid rgba(90,174,239,0.08);
            font-size: 13px;
        }
        .crew-row:last-child { border-bottom: none; }
        .crew-role {
            font-size: 10px;
            text-transform: uppercase;
            color: #6ab4d4;
            letter-spacing: 0.5px;
            min-width: 36px;
            padding-top: 1px;
        }
        .crew-name { color: #eaf6ff; }
        /* &#9472;&#9472; NOTAM &#9472;&#9472; */
        .notam-pinned-bar {
            background: rgba(10,30,50,0.7);
            border: 1px solid #f5a623;
            border-radius: 6px;
            margin-bottom: 10px;
            overflow: hidden;
        }
        .notam-pinned-title {
            background: rgba(245,166,35,0.15);
            padding: 8px 12px;
            font-size: 11px;
            font-weight: 700;
            color: #f5a623;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .notam-pinned-body { padding: 4px 0; }
        .notam-sub-section {
            margin-bottom: 6px;
            border: 1px solid rgba(90,174,239,0.15);
            border-radius: 6px;
            overflow: hidden;
        }
        .notam-sub-header {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 9px 12px;
            cursor: pointer;
            background: rgba(10,25,40,0.4);
            user-select: none;
        }
        .notam-sub-header:active { background: rgba(90,174,239,0.08); }
        .notam-airport-role-badge {
            font-size: 9px;
            font-weight: 700;
            color: #fff;
            background: rgba(30,96,145,0.9);
            border-radius: 3px;
            padding: 2px 6px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            flex-shrink: 0;
        }
        .notam-sub-title {
            font-size: 13px;
            font-weight: 700;
            color: #7ad8fd;
            flex: 1;
        }
        .notam-sub-count {
            font-size: 11px;
            font-weight: 700;
            color: #fff;
            background: rgba(122,216,253,0.2);
            border-radius: 10px;
            padding: 1px 7px;
            flex-shrink: 0;
        }
        .notam-sub-arrow {
            font-size: 10px;
            color: #7ad8fd;
            transition: transform 0.2s;
            flex-shrink: 0;
        }
        .notam-sub-arrow.collapsed { transform: rotate(-90deg); }
        .notam-sub-body {
            padding: 0 10px 6px 10px;
        }
        .notam-category-bar {
            font-size: 10px;
            font-weight: 700;
            color: #6ab4d4;
            background: transparent;
            padding: 3px 8px;
            margin: 8px 0 4px 0;
            border-left: 3px solid #7ad8fd;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        /* &#9472;&#9472; Weather section &#9472;&#9472; */
        .wx-text {
            padding: 6px 10px 8px 10px;
        }
        .wx-text pre {
            color: #d8f0ff;
            font-size: 12px;
            white-space: pre-wrap;
            word-break: break-all;
            margin: 0;
        }
        .wx-nil {
            padding: 6px 10px;
            color: #6ab4d4;
            font-size: 12px;
            font-style: italic;
        }
        .notam-entry {
            background: rgba(10,25,40,0.5);
            border: 1px solid rgba(90,174,239,0.08);
            border-radius: 4px;
            margin-bottom: 5px;
            overflow: hidden;
        }
        .notam-entry.expired { opacity: 0.5; }
        .notam-entry.pinned { border-color: #f5a623; }
        .notam-entry-hdr {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 10px;
            background: rgba(30,80,110,0.4);
            border-bottom: 1px solid rgba(90,174,239,0.1);
            gap: 8px;
            flex-wrap: wrap;
        }
        .notam-entry.pinned .notam-entry-hdr { background: rgba(245,166,35,0.08); }
        .notam-id {
            font-size: 12px;
            font-weight: 700;
            color: #d8f0ff;
            font-family: 'SF Mono', 'Courier New', monospace;
        }
        .notam-meta {
            font-size: 10px;
            color: #6ab4d4;
            font-family: 'SF Mono', 'Courier New', monospace;
            flex: 1;
        }
        .notam-pin-btn {
            background: none;
            border: 1px solid rgba(90,174,239,0.3);
            border-radius: 4px;
            color: #6ab4d4;
            font-size: 13px;
            cursor: pointer;
            padding: 2px 7px;
            line-height: 1;
            transition: all 0.15s;
            flex-shrink: 0;
        }
        .notam-pin-btn:hover { border-color: #f5a623; color: #f5a623; }
        .notam-pin-btn.pinned-active { border-color: #f5a623; color: #f5a623; background: rgba(245,166,35,0.1); }
        .notam-body {
            padding: 6px 10px;
            font-size: 12px;
            font-family: 'SF Mono', 'Courier New', monospace;
            color: #d8f0ff;
            white-space: pre-wrap;
            word-break: break-word;
            line-height: 1.5;
        }
        .notam-expired-div {
            text-align: center;
            font-size: 10px;
            color: #9a8870;
            padding: 4px 0;
            letter-spacing: 1px;
            border-top: 1px dashed rgba(154,136,112,0.3);
            border-bottom: 1px dashed rgba(154,136,112,0.3);
            margin: 6px 0 3px 0;
        }
        /* &#9472;&#9472; FILE LINK &#9472;&#9472; */
        .file-link {
            display: inline-block;
            color: #7ad8fd;
            text-decoration: none;
            font-size: 13px;
        }
        .file-link:hover { text-decoration: underline; }
        /* &#9472;&#9472; IMAGES &#9472;&#9472; */
        .image-container img {
            max-width: 100%;
            border-radius: 5px;
            border: 1px solid rgba(90,174,239,0.2);
            margin-top: 8px;
        }
        /* &#9472;&#9472; NAVLOG TOGGLE &#9472;&#9472; */
        .navlog-toggle {
            cursor: pointer; user-select: none;
            background: linear-gradient(160deg, #1a4a61 0%, #21546D 60%, #1c4a60 100%);
            border: 1px solid rgba(90,174,239,0.12);
            border-radius: 8px;
            padding: 14px 16px;
            font-weight: 600; font-size: 16px;
            color: #d8f0ff;
            display: flex; align-items: center; gap: 10px;
            letter-spacing: 0.3px;
            margin-bottom: 10px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.3);
        }
        .navlog-toggle .nav-icon { color: #7ad8fd; font-size: 17px; }
        .navlog-toggle .collapse-arrow { margin-left: auto; color: #7ad8fd; font-size: 12px; transition: transform 0.2s ease; }
        .navlog-toggle.collapsed .collapse-arrow { transform: rotate(-90deg); }
        /* &#9472;&#9472; BOTTOM NAV BAR &#9472;&#9472; */
        .bottom-nav {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: linear-gradient(90deg, #0e3a52 0%, #1a4a61 100%);
            border-top: 1px solid #2a6a8a;
            display: flex;
            justify-content: space-around;
            padding: 8px 0 env(safe-area-inset-bottom, 8px) 0;
            z-index: 800;
        }
        .bottom-nav-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 3px;
            padding: 2px 8px;
            color: #4a7a9a;
            font-size: 9px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            cursor: pointer;
        }
        .bottom-nav-item.active { color: #7ad8fd; }
        .bottom-nav-icon { font-size: 18px; }
        @media (max-width: 600px) {
            .fw-grid { grid-template-columns: repeat(2, 1fr); }
        }
        label { display: none; }
        /* &#9472;&#9472; NAVLOG TABLE &#9472;&#9472; */
        /* navlog-table styles injected dynamically per-flight */
        .table-wrapper {
            overflow-x: auto;
            margin-bottom: 10px;
            border: 1px solid #1e5a78;
            border-radius: 6px;
            overflow: hidden;
        }
        .file-link {
            display: inline-block;
            margin-top: 4px;
            color: #7ad8fd;
            text-decoration: none;
            font-size: 13px;
        }
        .file-link:hover { text-decoration: underline; }
    </style>
    """

    collapse_js = """
<script>
function toggleSection(headerId) {
    var hdr  = document.getElementById(headerId);
    var body = document.getElementById(headerId + '-body');
    if (!hdr || !body) return;
    var collapsed = body.classList.contains('collapsed');
    if (collapsed) {
        body.classList.remove('collapsed');
        hdr.classList.remove('collapsed');
    } else {
        body.classList.add('collapsed');
        hdr.classList.add('collapsed');
    }
    // Persist collapse state
    try { localStorage.setItem('collapse_' + headerId, collapsed ? '0' : '1'); } catch(e) {}
}
function toggleNotamSub(subId) {
    var body  = document.getElementById(subId);
    var arrow = document.getElementById(subId + '-arrow');
    if (!body) return;
    var isCollapsed = body.style.display === 'none';
    body.style.display  = isCollapsed ? '' : 'none';
    if (arrow) arrow.classList.toggle('collapsed', !isCollapsed);
}
window.addEventListener('load', function() {
    var KEEP_OPEN = 'sec-schedule';
    document.querySelectorAll('.section-header[id]').forEach(function(hdr) {
        var body = document.getElementById(hdr.id + '-body');
        if (!body) return;
        // Check localStorage first; if never set, collapse everything except the first section
        var stored;
        try { stored = localStorage.getItem('collapse_' + hdr.id); } catch(e) { stored = null; }
        var shouldCollapse = (stored !== null) ? (stored === '1') : (hdr.id !== KEEP_OPEN);
        if (shouldCollapse) {
            body.classList.add('collapsed');
            hdr.classList.add('collapsed');
        }
    });
});
</script>
"""
    html = ("<!DOCTYPE html><html><head>"
        "<meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0, viewport-fit=cover'>"
        "<title>Aviobook</title>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
        "<meta name='apple-mobile-web-app-title' content='Aviobook'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<link rel='manifest' href='/manifest.json'>"
        + css + collapse_js + "</head><body>")
    html += "<div class='container'>"

    # Extract data
    g = data['general']
    r = data['route']
    a = data['airports']
    ac = data['aircraft']
    f = data['fuel']
    t = data['times']
    c = data['crew']

    # &#9472;&#9472; ForeFlight deep-link: preload route from SimBrief XML &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    import urllib.parse as _urlparse
    _ff_orig  = a['origin']['icao']
    _ff_dest  = a['destination']['icao']
    _ff_route_str = r.get('route_ifps', '') or r.get('route', '') or ''
    # Strip leading/trailing airport identifiers if already present in the route string
    _ff_route_clean = _ff_route_str.strip()
    # ForeFlight deep link: open Maps with route search
    # foreflightmobile://maps/search?q=ORIG ROUTE DEST is the documented scheme
    _ff_route_q = f"APT@{_ff_orig} {_ff_route_clean} APT@{_ff_dest}".strip()
    _ff_url = "foreflightmobile://maps/search?q=" + _urlparse.quote(_ff_route_q, safe='')

    try:
        sched_off_utc, sched_off_loc   = t.get('sched_off', ("--:--", "--:--"))
        est_out_utc,   est_out_loc     = t.get('est_out',   ("--:--", "--:--"))
        sched_in_utc,  sched_in_loc    = t.get('sched_in',  ("--:--", "--:--"))
        est_in_utc,    est_in_loc      = t.get('est_in',    ("--:--", "--:--"))
    except Exception:
        sched_off_utc = sched_off_loc = est_out_utc = est_out_loc = "--:--"
        sched_in_utc  = sched_in_loc  = est_in_utc  = est_in_loc  = "--:--"

    initial_alt = int(g.get('initial_altitude', 0) or 0)
    cruise_alt = f"FL{initial_alt // 100}" if initial_alt > 18000 else f"{initial_alt} ft"

    # &#9472;&#9472; TOP BAR (sticky) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    # Derive departure date using LOCAL timezone so "30 MAR" matches local time
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        _ts = int(data.get('times', {}).get('sched_off_ts') or '0')
        _tz_off = int(data.get('general', {}).get('orig_timezone', '0') or '0')
        _dt_local = _dt.fromtimestamp(_ts, tz=_tz.utc) + _td(hours=_tz_off)
        _mon_abbr = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
        _dep_date_disp = f"{_dt_local.day:02d} {_mon_abbr[_dt_local.month-1]}"
    except Exception:
        _dep_date_disp = ''
    _dep_label = f"{_dep_date_disp} {sched_off_utc}".strip() if _dep_date_disp else sched_off_utc

    html += "<div class='top-bar'><div class='top-bar-inner'>"

    # Outer flex: [left+center text block] | [right icons]
    # The icons span both rows vertically
    html += "<div style='display:flex;align-items:center;justify-content:space-between;padding-bottom:0;'>"

    # &#9472;&#9472; LEFT+CENTER text block &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div style='flex:1;min-width:0;'>"

    # Row 1: time LEFT &middot; flight+reg PERFECT CENTER
    html += "<div style='display:grid;grid-template-columns:1fr auto 1fr;align-items:center;margin-bottom:3px;'>"

    # Left: UTC clock
    html += "<div style='text-align:left;'>"
    html += f"<span class='top-bar-time' id='utc-clock'>--:-- UTC</span>"
    html += "</div>"

    # Center: flight + aircraft  &#9992;SWA677&middot;&#8855;N766NC &ndash; B737-7H4(WL)
    html += ("<div style='display:flex;justify-content:center;align-items:center;gap:5px;'>"
             "<span style='color:#5ab8e0;font-size:14px;'>&#9992;</span>"
             f"<span class='top-bar-flt'>{g['icao_airline']}{g['flight_number']}</span>"
             "<span style='color:#7ab8d4;font-size:13px;'>&#xB7;</span>"
             "<span style='color:#4db8f5;font-size:13px;'>&#8855;</span>"
             f"<span class='top-bar-reg'>{ac.get('reg','')} &ndash; {ac.get('name', ac.get('type',''))}</span>"
             "</div>")

    # Right spacer
    html += "<div></div>"

    html += "</div>"

    # Row 2: route &mdash; &#9992;KSTL&#9992;KRSW 30 MAR 12:10&#8250;14:30  &mdash; centered under row 1
    html += "<div class='top-bar-row2' style='justify-content:center;'>"
    html += (f"<span style='color:#5ab8e0;font-size:12px;'>&#9992;</span>"
             f"<span class='icao'>{a['origin']['icao']}</span>"
             f"<span style='color:#5ab8e0;font-size:12px;'>&#9992;</span>"
             f"<span class='icao'>{a['destination']['icao']}</span>"
             f"<span style='color:#4a7a96;font-size:12px;margin-left:4px;'>&#128197;</span>"
             f"<span class='times'>{_dep_label}</span>"
             f"<span class='arrow'>&#8250;</span>"
             f"<span class='times'>{sched_in_utc}</span>")
    html += "</div>"

    html += "</div>"  # left+center text block

    # &#9472;&#9472; RIGHT icons &mdash; vertically centered across both rows &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='top-bar-icons'>"
    html += ("<button class='top-bar-icon-btn' onclick='window.location.href=\"/\"' title='Back to Launcher' "
             "style='font-size:15px;'>&#8962;</button>")   # &#8962; home
    html += ("<button class='top-bar-icon-btn' id='sign-btn' onclick='if(window.openSign)openSign()' title='Sign OFP'>"
             "&#9998;</button>")    # &#9998; pencil
    html += ("<button class='top-bar-icon-btn' title='Notifications'>"
             "&#128276;</button>")  #  bell
    html += ("<button class='top-bar-icon-btn' title='Profile'>"
             "<span style='font-size:16px;'>&#9711;</span>"
             "<span class='badge'>2</span>"
             "</button>")
    html += ("<button class='top-bar-icon-btn' onclick='openSettings()' title='Settings' id='settings-btn'>"
             "&#9881;</button>")   # &#9881; gear
    html += "</div>"

    html += "</div>"  # outer flex row

    # &#9472;&#9472; Tab bar (rendered by JS based on active bottom-nav section) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='tab-bar' id='main-tab-bar'></div>"

    html += "</div>"   # top-bar-inner
    html += "</div>"   # top-bar

    # &#9472;&#9472; GREEN BANNERS (fixed just below top-bar, visible across ALL tabs) &#9472;&#9472;&#9472;&#9472;
    _pic_name   = _captain_name or (c.get('dx') or c.get('fo') or 'PILOT').strip().upper()
    _ofp_rls    = data['ofp'].get('time', '1')
    _airline    = g.get('icao_airline', '')
    _flt_num    = g.get('flight_number', '')
    html += (
        f"<div id='sign-banners' style='display:none;width:100%;overflow:hidden;box-sizing:border-box;'>"
        # FFD banner &mdash; transparent bg, green border
        f"<div id='banner-ffd' style='"
        f"background:rgba(30,180,80,0.18);border-bottom:1px solid rgba(76,223,138,0.45);"
        f"padding:7px 14px;box-sizing:border-box;width:100%;overflow:hidden;"
        f"display:flex;align-items:center;gap:10px;'>"
        f"<span style='color:#4cdf8a;font-size:15px;font-weight:700;flex-shrink:0;'>&#10003;</span>"
        f"<span style='font-size:12px;font-weight:600;color:#4cdf8a;letter-spacing:0.2px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
        f"FIT FOR DUTY SIGNED ON <span id='banner-ffd-time'></span></span>"
        f"</div>"
        # OFP RLS banner &mdash; solid lime green, dark text (per reference)
        f"<div id='banner-ofp' style='"
        f"background:#32d96a;"
        f"padding:9px 14px;box-sizing:border-box;width:100%;overflow:hidden;"
        f"display:flex;align-items:center;gap:8px;'>"
        f"<span style='color:#0a2e14;font-size:15px;font-weight:700;flex-shrink:0;'>&#10003;</span>"
        f"<span style='font-size:12px;font-weight:700;color:#0a2e14;letter-spacing:0.2px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;'>"
        f"OFP RLS {_ofp_rls} ACCEPTED ON <span id='banner-ofp-time'></span></span>"
        f"<span style='font-size:9.5px;font-weight:600;color:#0a4020;letter-spacing:0.3px;flex-shrink:0;white-space:nowrap;'>"
        f"ID: <span id='banner-sub-id'></span></span>"
        f"</div>"
        f"</div>"
    )

    html += "<div class='content'>"

    # &#9472;&#9472; SCHEDULE SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-schedule' onclick='toggleSection(\"sec-schedule\")'>"
    html += "    <span class='section-icon'>&#9201;</span> Schedule"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-schedule-body' style='padding:8px 16px 10px 16px;'>"

    orig_iata = a['origin'].get('iata', '')
    dest_iata = a['destination'].get('iata', '')
    orig_name = a['origin'].get('name', '')
    dest_name = a['destination'].get('name', '')
    orig_gate = a['origin'].get('gate', '')
    dest_gate = a['destination'].get('gate', '')
    ofp_rls   = data['ofp'].get('time', '1')
    est_block_display = t.get('est_block') or t.get('sched_block') or '----'

    # &#9472;&#9472; ON TIME / DELAYED badge &mdash; based on OUT (pushback) time &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _sched_off_ts_val = int(data['times']['sched_off_ts'] or 0)
    _taxi_out_secs    = int(data['times'].get('taxi_out') or 0)
    _out_ts_val       = _sched_off_ts_val - _taxi_out_secs   # OUT = OFF minus taxi
    html += "  <div style='margin-bottom:6px;'>"
    html += f"    <span id='status-badge' class='on-time-badge' data-out-ts='{_out_ts_val}' onclick='pillTap()' title='Tap to start OUT &middot; Tap again for ON BLOCKS &middot; Tap to reset'>ON TIME</span>"
    html += "  </div>"

    # &#9472;&#9472; ICAO row &mdash; normal flow, flex left/right &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "  <div class='arc-icao-row'>"
    html += "    <div class='arc-icao-left'>"
    html += f"      <div class='arc-icao'>{a['origin']['icao']}</div>"
    iata_gate_left = f"{orig_iata}"
    if orig_name: iata_gate_left += f" - {orig_name}"
    if orig_gate: iata_gate_left += f"<br>GATE: {orig_gate}"
    html += f"      <div class='arc-iata-gate'>{iata_gate_left}</div>"
    html += "    </div>"
    html += "    <div class='arc-icao-right'>"
    html += f"      <div class='arc-icao'>{a['destination']['icao']}</div>"
    iata_gate_right = f"{dest_iata}"
    if dest_name: iata_gate_right += f" - {dest_name}"
    if dest_gate: iata_gate_right += f"<br>GATE: {dest_gate}"
    html += f"      <div class='arc-iata-gate'>{iata_gate_right}</div>"
    html += "    </div>"
    html += "  </div>"

    # &#9472;&#9472; CENTER &mdash; flight number, swoop, meta, times &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "  <div class='arc-center'>"
    html += f"    <div class='arc-flightnum'>{g['icao_airline']}{g['flight_number']}</div>"

    # &#9472;&#9472; Single full-width SVG: flight profile + dots + labels &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    # ViewBox 1000&times;160. Baseline at y=52. Profile hump above. Labels below.
    # Dots ON line at cy=52. Label at y=68 (light blue, small). Value at y=88 (white, bold).
    # Meta text above line inside cruise plateau.
    # Build local time strings and timezone label for each dot
    def _local_time(utc_hhmm, tz_offset_str):
        """Given HH:MM UTC and a timezone offset string, return (local_hhmm, tz_label)."""
        try:
            tz_h = int(tz_offset_str or 0)
        except (ValueError, TypeError):
            tz_h = 0
        if not utc_hhmm or utc_hhmm in ("--:--", "N/A"):
            sign = "+" if tz_h >= 0 else ""
            return "--:--", f"{sign}{tz_h}"
        try:
            h, m = int(utc_hhmm[:2]), int(utc_hhmm[3:5])
            total = (h * 60 + m + tz_h * 60) % (24 * 60)
            lh, lm = total // 60, total % 60
            sign = "+" if tz_h >= 0 else ""
            return f"{lh:02d}:{lm:02d}", f"{sign}{tz_h}"
        except Exception:
            sign = "+" if tz_h >= 0 else ""
            return "--:--", f"{sign}{tz_h}"

    orig_tz = g.get("orig_timezone", "0")
    dest_tz = g.get("dest_timezone", "0")

    dot_data = [
        (80,  "OUT",  est_out_utc,   *_local_time(est_out_utc,   orig_tz)),
        (175, "OFF",  sched_off_utc, *_local_time(sched_off_utc, orig_tz)),
        (270, "ETOT", sched_off_utc, *_local_time(sched_off_utc, orig_tz)),
        (730, "ELDT", est_in_utc,    *_local_time(est_in_utc,    dest_tz)),
        (825, "IN",   est_in_utc,    *_local_time(est_in_utc,    dest_tz)),
        (920, "IN",   sched_in_utc,  *_local_time(sched_in_utc,  dest_tz)),
    ]
    svg  = ("<svg viewBox='0 0 1000 160' xmlns='http://www.w3.org/2000/svg' "
            "style='width:100%;display:block;margin:2px 0 0 0;'>"
            # flight profile &mdash; baseline at y=80, peak at y=14
            "<path d='M 0 80 L 300 80 "
            "C 340 80 380 14 410 14 "
            "L 590 14 "
            "C 620 14 660 80 700 80 "
            "L 1000 80' "
            "fill='none' stroke='#7ad8fd' stroke-width='3.5' "
            "stroke-linecap='round' stroke-linejoin='round'/>"
            )
    # OFP RLS pill &mdash; black filled, clear gap below profile peak (peak y=14, pill y=26)
    svg += ("<rect x='435' y='26' width='130' height='22' rx='11' ry='11' "
            "fill='#000' stroke='#7ad8fd' stroke-width='1.5'/>"
            f"<text x='500' y='42' text-anchor='middle' "
            f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='13' font-weight='700' fill='#7ad8fd' "
            f"letter-spacing='0.5'>OFP RLS {ofp_rls}</text>")
    # Meta lines &mdash; 16px spacing, starting y=58
    meta_lines = [
        (f"FLIGHT TIME  {est_block_display}",          "700"),
        (f"GROUND DISTANCE  {g['route_distance']} NM", "400"),
        (f"MAX PLND  {f['plan_ramp']} LB",             "400"),
        (f"FL  {cruise_alt.replace('FL', '')}",        "400"),
    ]
    for i, (text, weight) in enumerate(meta_lines):
        parts = text.split("  ", 1)
        y = 60 + i * 16
        if len(parts) == 2:
            svg += (f"<text x='498' y='{y}' text-anchor='end' "
                    f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='12' font-weight='700' fill='#d8f0ff'>"
                    f"{parts[0]}</text>"
                    f"<text x='504' y='{y}' text-anchor='start' "
                    f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='12' font-weight='400' fill='#7ad8fd'>"
                    f"{parts[1]}</text>")
        else:
            svg += (f"<text x='500' y='{y}' text-anchor='middle' "
                    f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='12' font-weight='{weight}' fill='#d8f0ff'>"
                    f"{text}</text>")
    # Dots on baseline y=80, label y=95, UTC y=112, local y=127, tz y=139
    _dot_id_map = {270: 'svg-lbl-etot', 730: 'svg-lbl-eldt'}
    for x, lbl, val, loc_val, tz_lbl in dot_data:
        lbl_id = _dot_id_map.get(x, '')
        id_attr = f" id='{lbl_id}'" if lbl_id else ''
        svg += (
            f"<circle cx='{x}' cy='80' r='5.5' fill='#7ad8fd'/>"
            f"<text x='{x}' y='95' text-anchor='middle'{id_attr} "
            f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='12' font-weight='700' fill='#6ab4d4' "
            f"letter-spacing='1'>{lbl}</text>"
            f"<text x='{x}' y='112' text-anchor='middle' "
            f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='15' font-weight='400' fill='#eaf6ff'>"
            f"{val}</text>"
            f"<text x='{x}' y='127' text-anchor='middle' "
            f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='13' font-weight='400' fill='#7ad8fd'>"
            f"{loc_val}</text>"
            f"<text x='{x}' y='139' text-anchor='middle' "
            f"font-family=\"-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Arial,sans-serif\" font-size='10' font-weight='400' fill='#4e88aa'>"
            f"{tz_lbl}</text>"
        )
    svg += "</svg>"
    # Make the SVG / flight-profile area clickable &#8594; opens F&W overlay
    svg_wrapped = (
        "<div id='fw-trigger' style='cursor:pointer;' title='Tap for Fuel &amp; Weights'>"
        + svg +
        "</div>"
    )
    html += f"    {svg_wrapped}"
    html += "  </div>"  # arc-center

    html += "  </div>"  # section-body
    html += "</div>"    # section

    # &#9472;&#9472; DISPATCH SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-dispatch' onclick='toggleSection(\"sec-dispatch\")'>"
    html += "    <span class='section-icon'>&#127911;</span> Dispatch"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-dispatch-body'>"
    html += f"  <div class='data-row'><span class='data-label'>NAME</span><span class='data-value'>{data['ofp']['name']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>OFP RLS</span><span class='data-value'>{data['ofp']['time']}</span></div>"
    disp_phone = data['ofp'].get('telephone') or os.environ.get('AVIOBOOK_DISPATCH_PHONE', '')
    html += f"  <div class='data-row'><span class='data-label'>TELEPHONE</span><span class='data-value'>{_html_escape.escape(disp_phone)}</span></div>"
    if g.get('dx_rmk'):
        html += f"  <div class='data-row'><span class='data-label'>REMARKS</span></div>"
        html += f"  <div class='route-box'>{_html_escape.escape(g['dx_rmk'])}</div>"
    html += "  </div></div>"

    # &#9472;&#9472; ROUTE SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-route' onclick='toggleSection(\"sec-route\")'>"
    html += "    <span class='section-icon'>&#9685;</span> Route"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-route-body'>"
    route_text = r['navlog'] if r['navlog'] else r['route']
    html += "  <div style='margin-bottom:6px;'><span class='data-label'>ATC ROUTE</span></div>"
    html += f"  <div class='route-box'>{route_text}</div>"
    html += f"  <div class='data-row'><span class='data-label'>SID</span><span class='data-value'>{r['dep_rwy']}.{r['sid_ident']}.{r['sid_trans']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>STAR</span><span class='data-value'>{r['star_trans']}.{r['star_ident']}.{r['arr_rwy']}</span></div>"
    if r.get('stepclimb_string'):
        html += f"  <div class='data-row'><span class='data-label'>STEP CLIMB</span><span class='data-value'>{r['stepclimb_string']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>GDIS / ADIS</span><span class='data-value'>{g['route_distance']} / {g['air_distance']} NM</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>AVG WIND</span><span class='data-value'>{g['avg_wind_dir']}/{g['avg_wind_spd']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>ISA DEV</span><span class='data-value'>{g['avg_temp_dev']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>TROPO</span><span class='data-value'>{g['avg_tropopause']}</span></div>"
    html += f"  <div class='data-row'><span class='data-label'>COST INDEX</span><span class='data-value'>{g['cost_index']}</span></div>"
    html += "  </div></div>"

    # &#9472;&#9472; FUEL SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    f = data['fuel']
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-fuel' onclick='toggleSection(\"sec-fuel\")'>"
    html += "    <span class='section-icon'>&#9651;</span> Fuel"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-fuel-body'>"
    html += "  <div class='fw-grid'>"

    def add_fw(label, value, unit="LB"):
        nonlocal html
        if value:
            try:
                if int(float(value)) == 0:
                    return
            except Exception: pass
            html += f"<div class='fw-item'><div class='fw-label'>{label}</div><div class='fw-value'>{value}<span class='fw-unit'>{unit}</span></div></div>"

    add_fw('MAX PLND', f.get('plan_ramp'))
    add_fw('TAXI', f.get('taxi_out'))
    add_fw('BURN', f.get('block'))
    add_fw('RESERVE', f.get('reserve'))
    add_fw('ALTERNATE', f.get('alternate_burn'))
    add_fw('EXTRA', f.get('extra'))
    add_fw('MIN T/O', f.get('min_takeoff'))
    add_fw('LANDING', f.get('plan_landing'))
    for b in f.get('fuel_extra', []):
        if b['fuel'] > 0:
            html += f"<div class='fw-item'><div class='fw-label'>{b['label']}</div><div class='fw-value'>{b['fuel']}<span class='fw-unit'>LB</span></div></div>"
    html += "  </div>"
    # CI row
    html += f"  <div class='data-row' style='margin-top:10px;'><span class='data-label'>COST INDEX</span><span class='data-value'>{g['cost_index']}</span></div>"
    html += "  </div></div>"

    # &#9472;&#9472; WEIGHTS SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    w = data['weights']
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-weights' onclick='toggleSection(\"sec-weights\")'>"
    html += "    <span class='section-icon'>&#9878;</span> Weights"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-weights-body'>"
    html += "  <div class='fw-grid'>"
    add_fw('OEW', w.get('oew'))
    add_fw('PAYLOAD', w.get('payload'))
    add_fw('ZFW', w.get('zero_fuel'))
    add_fw('TOW', w.get('takeoff'))
    add_fw('LDW', w.get('landing'))
    add_fw('CARGO', w.get('cargo'))
    if g.get('passengers'):
        html += f"<div class='fw-item'><div class='fw-label'>PAX</div><div class='fw-value'>{g['passengers']}</div></div>"
    html += "  </div></div></div>"

    # &#9472;&#9472; ALTERNATE AIRPORTS &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    alternates = data.get('alternate', [])
    if alternates:
        html += "<div class='section'>"
        html += "  <div class='section-header' id='sec-alternate' onclick='toggleSection(\"sec-alternate\")'>"
        html += "    <span class='section-icon'>&#9992;</span> Alternate Airports"
        html += "    <span class='collapse-arrow'>&#9660;</span>"
        html += "  </div>"
        html += "  <div class='section-body' id='sec-alternate-body'>"
        dest_count = 0
        for alt in alternates:
            alt_type = alt.get('type', 'DEST')
            if alt_type == 'TKOF':
                label = 'TKOF ALTN'
            elif alt_type == 'ENRTE':
                label = 'ENRTE ALTN'
            else:
                dest_count += 1
                label = f'ALTN {dest_count}' if dest_count > 1 else 'ALTN'
            ete_seconds = int(alt.get('ete', 0))
            ete_h = ete_seconds // 3600
            ete_m = (ete_seconds % 3600) // 60
            try:
                alt_cruise = int(alt.get('cruise_altitude', 0))
                alt_fl = f"FL{alt_cruise//100}" if alt_cruise > 18000 else f"{alt_cruise}ft"
            except Exception: alt_fl = "---"
            html += f"  <div class='alt-block'>"
            name_str = f" &mdash; {alt['icao']}" + (f" / {alt['iata']}" if alt['iata'] else '') + (f"  {alt['name']}" if alt['name'] else '')
            html += f"    <div class='alt-title'>{label}{name_str}</div>"
            if alt['distance'] and alt['distance'] != '0':
                html += f"    <div class='data-row'><span class='data-label'>DISTANCE</span><span class='data-value'>{alt['distance']} NM</span></div>"
            if ete_h or ete_m:
                html += f"    <div class='data-row'><span class='data-label'>ETE</span><span class='data-value'>{ete_h}h {ete_m:02d}m</span></div>"
            if alt['burn'] and alt['burn'] != '0':
                html += f"    <div class='data-row'><span class='data-label'>BURN</span><span class='data-value'>{alt['burn']} LB</span></div>"
            if alt_fl != '---':
                html += f"    <div class='data-row'><span class='data-label'>FL</span><span class='data-value'>{alt_fl}</span></div>"
            # Extra fields for TKOF / ENRTE
            if alt_type in ('TKOF', 'ENRTE'):
                if alt.get('elevation'):
                    html += f"    <div class='data-row'><span class='data-label'>ELEVATION</span><span class='data-value'>{alt['elevation']} ft</span></div>"
                if alt.get('trans_alt'):
                    html += f"    <div class='data-row'><span class='data-label'>TRANS ALT</span><span class='data-value'>{alt['trans_alt']}</span></div>"
                if alt.get('trans_level'):
                    html += f"    <div class='data-row'><span class='data-label'>TRANS LVL</span><span class='data-value'>{alt['trans_level']}</span></div>"
                if alt.get('metar'):
                    cat = alt.get('metar_category', '').upper()
                    cat_color = {'VFR': '#66ff66', 'MVFR': '#66aaff', 'IFR': '#ff6666', 'LIFR': '#ff44ff'}.get(cat, '#d8f0ff')
                    html += (f"    <div class='data-row'><span class='data-label'>METAR"
                             + (f" <span style='color:{cat_color};font-size:10px;'>{cat}</span>" if cat else '')
                             + f"</span><span class='data-value' style='font-size:11px;font-family:monospace;white-space:pre-wrap;word-break:break-all;'>{alt['metar']}</span></div>")
                if alt.get('taf'):
                    html += (f"    <div class='data-row'><span class='data-label'>TAF</span>"
                             f"<span class='data-value' style='font-size:11px;font-family:monospace;white-space:pre-wrap;word-break:break-all;'>{alt['taf']}</span></div>")
            if alt.get('route'):
                html += f"    <div class='route-box'><textarea style='width:100%;background:transparent;border:none;color:#eaf6ff;font-family:inherit;font-size:16px;resize:vertical;min-height:48px;outline:none;'>{alt['route']}</textarea></div>"
            html += "  </div>"
        html += "  </div></div>"

    # &#9472;&#9472; CREW SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='section'>"
    html += "  <div class='section-header' id='sec-crew' onclick='toggleSection(\"sec-crew\")'>"
    html += "    <span class='section-icon'>&#128100;</span> Crew"
    html += "    <span class='collapse-arrow'>&#9660;</span>"
    html += "  </div>"
    html += "  <div class='section-body' id='sec-crew-body'>"
    for role, name in [("CPT", _captain_name or c.get('cpt')), ("F/O", c.get('fo')), ("PU", c.get('pu'))]:
        if name:
            html += f"  <div class='crew-row'><span class='crew-role'>{role}</span><span class='crew-name'>{name}</span></div>"
    for fa in c.get('fa', []):
        if fa:
            html += f"  <div class='crew-row'><span class='crew-role'>FA</span><span class='crew-name'>{fa}</span></div>"
    html += "  </div></div>"

    # &#9472;&#9472; DOCUMENTS SECTION &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    if data.get('files'):
        html += "<div class='section'>"
        html += "  <div class='section-header' id='sec-documents' onclick='toggleSection(\"sec-documents\")'>"
        html += "    <span class='section-icon'>&#128196;</span> Documents"
        html += "    <span class='collapse-arrow'>&#9660;</span>"
        html += "  </div>"
        html += "  <div class='section-body' id='sec-documents-body'>"
        for file in data['files']:
            html += f"  <div class='data-row'><span class='data-label'>{file['name']}</span>"
            html += f"  <a href='{file['link']}' target='_blank' class='file-link'>View &#8599;</a></div>"
        html += "  </div></div>"

    # &#9472;&#9472; INTERACTIVE NAVLOG (TOC &#8594; TOD) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    navlog_data  = data.get('navlog', {})
    navlog_fixes = navlog_data.get('fixes', [])
    plan_ramp    = navlog_data.get('plan_ramp', 0)
    sched_off    = navlog_data.get('sched_off_hhmm', '0000')
    flight_key   = navlog_data.get('flight_key', 'FLT')

    # Serialize fix data for JS &mdash; include ALL fixes (TOC, TOD, and synthetic DEST)
    import json as _json
    dest_icao_js   = data['airports']['destination']['icao']
    try:
        est_enroute_s  = int(data['times'].get('est_time_enroute', '0000').replace(':','') or 0)
        # convert HHMM to seconds
        ete_str = data['times'].get('est_time_enroute', '0000')
        ete_h, ete_m = int(ete_str[:2]), int(ete_str[2:])
        est_enroute_s = ete_h * 3600 + ete_m * 60
    except Exception:
        est_enroute_s = 0
    try:
        dest_fuel_used = int(float(data['fuel'].get('plan_ramp', 0) or 0)) - int(float(data['fuel'].get('plan_landing', 0) or 0))
    except Exception:
        dest_fuel_used = 0

    all_js_fixes = [
        {
            'ident':        fix['ident'],
            'cum_time_sec': fix['cum_time_sec'],
            'cum_fuel_used':fix['cum_fuel_used'],
        }
        for fix in navlog_fixes
    ]
    all_js_fixes.append({
        'ident':        dest_icao_js,
        'cum_time_sec': est_enroute_s,
        'cum_fuel_used':dest_fuel_used,
    })
    fixes_json = _json.dumps(all_js_fixes)

    navlog_css_js = f"""
<style>
/* &#9472;&#9472; entry overlay &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
#entry-overlay{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(10,21,32,0.97);z-index:1100;align-items:center;justify-content:center;}}
#entry-overlay.visible{{display:flex;}}
#entry-card{{background:linear-gradient(160deg,#1a4a61 0%,#21546D 60%,#1c4a60 100%);border:1px solid rgba(90,174,239,0.2);border-radius:8px;padding:28px 32px;min-width:300px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,0.6);}}
#entry-card h2{{margin:0 0 20px;color:#7ad8fd;font-size:18px;letter-spacing:1px;}}
.entry-field{{margin-bottom:16px;text-align:left;}}
.entry-field label{{display:block;color:#6ab4d4;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:5px;}}
.entry-field input{{width:100%;box-sizing:border-box;background:rgba(255,255,255,0.08);border:1px solid rgba(150,210,245,0.35);color:#fff;font-size:20px;font-weight:bold;padding:9px 12px;border-radius:4px;text-align:center;letter-spacing:3px;}}
.entry-field input:focus{{outline:none;border-color:#7ad8fd;}}
.entry-hint{{font-size:11px;color:#9ec8e0;margin-top:4px;}}
#entry-submit{{background:linear-gradient(90deg,#1a6a9a,#1e7db8);color:#fff;border:none;padding:12px 0;border-radius:4px;font-size:15px;font-weight:bold;cursor:pointer;width:100%;margin-top:8px;letter-spacing:1px;text-transform:uppercase;}}

/* &#9472;&#9472; waypoint popup &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
#wp-overlay{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(10,21,32,0.88);z-index:1100;align-items:center;justify-content:center;}}
#wp-overlay.visible{{display:flex;}}
#wp-card{{background:linear-gradient(160deg,#1a4a61 0%,#21546D 60%,#1c4a60 100%);border:1px solid rgba(90,174,239,0.25);border-radius:10px;padding:24px;width:360px;max-width:92vw;box-shadow:0 8px 40px rgba(0,0,0,0.7);font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;}}
#wp-card .wp-title{{text-align:center;color:#eaf6ff;font-size:18px;font-weight:700;letter-spacing:2px;margin-bottom:2px;}}
#wp-card .wp-sub{{text-align:center;color:#eaf6ff;font-size:15px;font-weight:700;letter-spacing:1px;margin-bottom:6px;}}
#wp-weights-lbl{{text-align:center;font-size:10px;color:#6ab4d4;letter-spacing:.8px;text-transform:uppercase;margin-bottom:16px;}}
.wp-cols{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px;}}
.wp-col label{{display:block;font-size:10px;color:#6ab4d4;text-transform:uppercase;letter-spacing:.7px;margin-bottom:3px;text-align:center;}}
.wp-col .wp-plnd-val{{text-align:center;font-size:14px;color:#eaf6ff;font-weight:600;padding:4px 0 6px;}}
.wp-col input{{width:100%;box-sizing:border-box;background:rgba(0,0,0,.3);border:1px solid rgba(150,210,245,.3);color:#fff;font-size:14px;font-weight:600;padding:7px 4px;border-radius:4px;text-align:center;letter-spacing:1px;}}
.wp-col input:focus{{outline:none;border-color:#7ad8fd;background:rgba(255,255,255,.1);}}
.wp-next{{margin-bottom:14px;}}
.wp-next label{{font-size:10px;color:#6ab4d4;text-transform:uppercase;letter-spacing:.7px;display:block;margin-bottom:4px;}}
.wp-next input{{width:100%;box-sizing:border-box;background:rgba(0,0,0,.3);border:1px solid rgba(150,210,245,.3);color:#fff;font-size:14px;padding:8px 12px;border-radius:4px;text-align:center;letter-spacing:1px;}}
.wp-next input:focus{{outline:none;border-color:#7ad8fd;}}
/* fuel warnings */
#wp-fuel-warn{{display:none;border-radius:6px;padding:10px 14px;margin-bottom:14px;font-size:12px;font-weight:600;letter-spacing:.3px;line-height:1.4;text-align:center;}}
#wp-fuel-warn.warn-critical{{display:block;background:rgba(200,40,40,0.25);border:1px solid rgba(220,60,60,0.6);color:#ff6b6b;}}
#wp-fuel-warn.warn-caution{{display:block;background:rgba(200,150,0,0.2);border:1px solid rgba(220,180,0,0.5);color:#ffd060;}}
.wp-btns{{display:flex;gap:10px;}}
.wp-btn-cancel{{flex:1;background:transparent;color:#6ab4d4;border:1px solid rgba(90,174,239,.4);padding:11px 0;border-radius:5px;font-size:13px;font-weight:600;cursor:pointer;text-transform:uppercase;letter-spacing:1px;}}
.wp-btn-done{{flex:2;background:linear-gradient(90deg,#1a6a9a,#1e7db8);color:#fff;border:none;padding:11px 0;border-radius:5px;font-size:14px;font-weight:700;cursor:pointer;text-transform:uppercase;letter-spacing:1px;}}

/* &#9472;&#9472; navlog: status bar &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
#nl-status{{
    display:flex;align-items:center;
    background:#0b1f30;
    border-bottom:1px solid #1a3a50;
    padding:0;
    min-height:60px;
}}
.nl-sb-col{{
    flex:1;display:flex;flex-direction:column;justify-content:center;
    gap:3px;padding:10px 16px;
    border-right:1px solid #1a3a50;
}}
.nl-sb-col:last-child{{border-right:none;}}
.nl-sb-lbl{{
    font-size:9px;color:#4e7a96;text-transform:uppercase;
    letter-spacing:.8px;font-weight:600;line-height:1.4;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}
.nl-sb-val{{
    font-size:17px;color:#eaf6ff;font-weight:700;letter-spacing:.2px;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}

/* &#9472;&#9472; navlog: takeoff bar &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
#nl-toff-bar{{
    display:flex;align-items:center;gap:8px;
    background:#1a4a61;
    border-bottom:1px solid rgba(90,174,239,0.15);
    padding:8px 12px;
    flex-wrap:wrap;
}}
#nl-toff-lbl,#nl-fuel-lbl{{
    font-size:10px;color:#6ab4d4;text-transform:uppercase;
    letter-spacing:.8px;font-weight:600;white-space:nowrap;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}
#nl-toff-inp,#nl-fuel-inp{{
    width:5em;background:rgba(0,0,0,0.3);
    border:1px solid rgba(90,174,239,0.35);
    color:#eaf6ff;font-size:15px;font-weight:700;
    padding:6px 8px;border-radius:5px;text-align:center;letter-spacing:3px;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
    outline:none;
}}
#nl-fuel-inp{{width:6em;letter-spacing:1px;}}
#nl-toff-inp:focus,#nl-fuel-inp:focus{{border-color:#7ad8fd;}}
#nl-apply-btn{{
    background:linear-gradient(90deg,#1a6a9a,#1e7db8);color:#fff;border:none;
    padding:8px 16px;border-radius:5px;font-size:12px;font-weight:700;
    cursor:pointer;text-transform:uppercase;letter-spacing:.5px;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}
#nl-apply-btn:active{{opacity:.85;}}
#nl-reset-btn{{
    margin-left:auto;background:transparent;color:#4e7a96;
    border:1px solid rgba(90,174,239,0.2);padding:8px 12px;
    border-radius:5px;font-size:11px;cursor:pointer;
    text-transform:uppercase;letter-spacing:.5px;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}

/* &#9472;&#9472; navlog: table wrapper &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
#nl-table-wrap{{padding:0 12px 16px;}}

/* &#9472;&#9472; column header row &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
/* Two rows: titles span sub-cols, then sub-labels — both use same grid as .nl-row */
.nlh-row,.nlh-subs-row{{
    display:grid;
    grid-template-columns: 22% 42px 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 4px;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
    padding-left:16px;
    padding-right:0;
}}
.nlh-row{{padding-top:12px;padding-bottom:0;align-items:end;}}
.nlh-subs-row{{padding-top:2px;padding-bottom:6px;}}
.nlh-fix-spc{{/* spacer col 1 */}}
.nlh-ma-spc{{
    text-align:center;font-size:9px;color:#4e7a96;
    font-weight:600;letter-spacing:.5px;text-transform:uppercase;
    padding-bottom:2px;
}}
.nlh-fl-title{{
    grid-column:span 2;font-size:11px;font-weight:700;letter-spacing:.4px;
    text-transform:uppercase;text-align:center;color:#7ad8fd;
}}
.nlh-ov-title{{
    grid-column:span 3;font-size:11px;font-weight:700;letter-spacing:.4px;
    text-transform:uppercase;text-align:center;color:#4cdf8a;
}}
.nlh-fob-title{{
    grid-column:span 3;font-size:11px;font-weight:700;letter-spacing:.4px;
    text-transform:uppercase;text-align:center;color:#7ad8fd;
}}
.nlh-bar-spc{{/* 4px swipe bar spacer */}}
.nlh-sub{{
    font-size:9px;color:#4e7a96;font-weight:600;
    letter-spacing:.5px;text-transform:uppercase;text-align:center;
}}

/* &#9472;&#9472; navlog cards &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472; */
.nl-card{{
    border-radius:6px;
    overflow:hidden;
    margin-bottom:4px;
    background:rgba(255,255,255,0.06);
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}
.nl-card.nl-special{{background:rgba(255,255,255,0.03);}}

/* same grid as header rows */
.nl-row{{
    display:grid;
    grid-template-columns: 22% 42px 1fr 1fr 1fr 1fr 1fr 1fr 1fr 1fr 4px;
    align-items:center;
    padding:0 0 0 16px;
    cursor:pointer;
    min-height:52px;
}}
.nl-row:active{{background:rgba(90,174,239,0.06);}}

/* fix name — same white as all data */
.nl-fix{{
    font-size:13px;color:#eaf6ff;font-weight:400;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    padding:14px 8px 14px 0;
}}
.nl-special .nl-fix{{
    color:#eaf6ff;font-size:13px;font-weight:400;
    letter-spacing:0;text-transform:none;
}}

/* MA — white like all data */
.nl-ma{{
    text-align:center;
    font-size:13px;color:#eaf6ff;font-weight:400;
    font-variant-numeric:tabular-nums;
    padding:14px 4px;
}}
.nl-special .nl-ma{{color:transparent;}}

/* data cells */
.nl-c{{
    text-align:center;
    font-size:13px;color:#eaf6ff;
    font-variant-numeric:tabular-nums;
    white-space:nowrap;overflow:hidden;
    padding:14px 2px;
}}
.nl-plnd{{color:#eaf6ff;font-weight:400;font-variant-numeric:tabular-nums;}}
.nl-act{{color:#eaf6ff;font-weight:500;font-variant-numeric:tabular-nums;}}
.nl-trend{{
    text-align:center;
    font-size:13px;font-weight:600;color:transparent;
    font-variant-numeric:tabular-nums;
    white-space:nowrap;padding:14px 2px;
}}
.nl-trend.pos{{color:#4cdf8a;}}
.nl-trend.neg{{color:#e05858;}}

/* right swipe bar + animated clock hint */
.nl-swipe-bar{{
    align-self:stretch;
    background:#1a5a7a;
    border-radius:0 6px 6px 0;
    position:relative;
    overflow:hidden;
    transition:width .15s ease;
    width:4px;
}}
/* clock icon revealed on swipe — shown via JS adding .swiping class to card */
.nl-card.swiping .nl-swipe-bar{{
    width:38px;
    background:rgba(0,200,200,0.25);
    display:flex;align-items:center;justify-content:center;
}}
.nl-swipe-clock{{
    display:none;
    position:absolute;right:0;top:0;bottom:0;width:38px;
    align-items:center;justify-content:center;
    font-size:18px;
    color:#4de8ff;
    pointer-events:none;
}}
.nl-card.swiping .nl-swipe-clock{{display:flex;}}

/* expanded detail panel */
.nl-detail{{
    background:rgba(0,0,0,0.2);
    padding:10px 16px 14px;
}}
.nl-detail-grid{{
    display:grid;
    grid-template-columns:repeat(6,1fr);
    gap:10px 8px;
}}
.nl-dg-lbl{{
    font-size:9px;color:#4e7a96;text-transform:uppercase;
    letter-spacing:.7px;font-weight:600;margin-bottom:2px;
}}
.nl-dg-val{{font-size:13px;color:#eaf6ff;font-weight:400;}}

/* FIR label */
.nl-fir-label{{
    font-size:10px;color:#4e7a96;font-weight:700;
    text-transform:uppercase;letter-spacing:.6px;
    padding:10px 16px 4px;cursor:pointer;
    font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;
}}
.nl-fir-chev{{
    float:right;font-size:10px;color:#4e7a96;
    transition:transform .2s;display:inline-block;
}}
</style>

<script>
var NAV_FIXES = {fixes_json};
var FLIGHT_KEY = '{flight_key}';
var PLAN_ELDT    = '{est_in_utc.replace(":", "") if est_in_utc and est_in_utc not in ("--:--","") else ""}';
var PLAN_ZFW     = {int(float(data['weights'].get('zero_fuel') or 0))};
var PLAN_MAX_LDW = {int(float(data['weights'].get('max_ldw') or 0))};
var PLAN_LDG_FUEL= {int(float(data['fuel'].get('plan_landing') or 0))};
var _wpIdent = null;

function hhmm2mins(s) {{
    s = String(s||'0000').replace(':','').replace('\u2014\u2014','0000').padStart(4,'0');
    return parseInt(s.slice(0,2))*60+parseInt(s.slice(2));
}}
function mins2hhmm(m) {{
    m=((m%1440)+1440)%1440;
    return String(Math.floor(m/60)).padStart(2,'0')+String(m%60).padStart(2,'0');
}}
function addSecsToHHMM(hhmm,secs) {{ return mins2hhmm(hhmm2mins(hhmm)+Math.round(secs/60)); }}

function applyEntryValues() {{
    var raw=document.getElementById('input-toff').value.replace(':','').trim();
    var il=document.getElementById('navlog-toff');
    if(il&&il.value&&!raw) raw=il.value.replace(':','').trim();
    if(il) il.value=raw;
    var toff=(raw.length===4&&/^[0-9]+$/.test(raw))?raw:'{sched_off}';
    // Prefer inline fuel input if the user typed there, else fall back to entry-overlay field
    var inlineFuelEl=document.getElementById('nl-fuel-inp');
    var inlineFuelVal=inlineFuelEl&&inlineFuelEl.value.trim();
    if(inlineFuelVal) document.getElementById('input-fuel').value=inlineFuelVal;
    var fuel=parseInt(document.getElementById('input-fuel').value)||{plan_ramp};
    NAV_FIXES.forEach(function(fix) {{
        var card=document.querySelector('.nl-card[data-ident="'+fix.ident+'"]');
        if(!card) return;
        var plndEt  =addSecsToHHMM(toff,fix.cum_time_sec);
        var plndFuel=fuel-fix.cum_fuel_used;
        card.dataset.plndEt=plndEt; card.dataset.plndFuel=plndFuel;
        var pE=card.querySelector('.p-et'), pF=card.querySelector('.p-fuel');
        if(pE) pE.textContent=plndEt.slice(0,2)+':'+plndEt.slice(2);
        if(pF) pF.textContent=plndFuel;
    }});
    try {{ localStorage.setItem(FLIGHT_KEY+'_toff',toff); localStorage.setItem(FLIGHT_KEY+'_fuel',fuel); }} catch(e) {{}}
    // Sync resolved values back to inline bar so both inputs always agree
    var _toffInpEl=document.getElementById('nl-toff-inp');
    var _fuelInpEl=document.getElementById('nl-fuel-inp');
    if(_toffInpEl) _toffInpEl.value=toff;
    if(_fuelInpEl) _fuelInpEl.value=String(fuel);
    document.getElementById('entry-overlay').classList.remove('visible');
    if(window.updateStatusBadge) updateStatusBadge();
    // Flip ETOT &#8594; ATOT once a real T/O time has been entered
    var etotEl = document.getElementById('svg-lbl-etot');
    if(etotEl) etotEl.textContent = 'ATOT';
}}

function resetAllValues() {{
    document.querySelectorAll('.nl-card[data-ident]').forEach(function(card) {{
        card.dataset.actEt=''; card.dataset.actFuel=''; card.dataset.actAlt='';
        refreshRow(card);
    }});
    try {{ Object.keys(localStorage).filter(function(k){{return k.startsWith(FLIGHT_KEY);}}).forEach(function(k){{localStorage.removeItem(k);}}); }} catch(e) {{}}
    // Clear and reset inline bar fields to plan defaults
    var toffInp=document.getElementById('nl-toff-inp');
    var fuelInp=document.getElementById('nl-fuel-inp');
    var toffHid=document.getElementById('input-toff');
    var fuelHid=document.getElementById('input-fuel');
    if(toffInp) toffInp.value='{sched_off}';
    if(fuelInp) fuelInp.value='{plan_ramp}';
    if(toffHid) toffHid.value='{sched_off}';
    if(fuelHid) fuelHid.value='{plan_ramp}';
    if(window.updateStatusBadge) updateStatusBadge();
    document.getElementById('entry-overlay').classList.add('visible');
}}

function updateEntryDelta(fid,orig,did,isTime) {{
    var el=document.getElementById(fid),dd=document.getElementById(did);
    if(!el||!dd) return;
    var cur=el.value.replace(':','').trim();
    if(!isTime) {{
        var d=(parseInt(cur)||0)-(parseInt(orig)||0);
        dd.textContent=d===0?'':(d>0?'+':'')+d+' lbs'; dd.style.color=d>0?'#66ff66':d<0?'#ff6666':'';
    }} else {{
        var d=hhmm2mins(cur.length===4?cur:orig)-hhmm2mins(String(orig));
        dd.textContent=d===0?'':(d>0?'+':'')+d+' min'; dd.style.color=d>0?'#ff6666':d<0?'#66ff66':'';
    }}
}}

// Swipe-left &#8594; open confirm popup | tap &#8594; expand detail (handled by onclick)
(function() {{
    var sx=0, sy0=0, tr=null, swiping=false, locked=false, moved=false;

    function flash(card, cb) {{
        card.style.transition = 'background 0.12s';
        card.style.background = 'rgba(74,168,218,0.2)';
        setTimeout(function() {{
            card.style.transition = 'background 0.2s';
            card.style.background = '';
            setTimeout(function() {{ card.style.transition=''; if(cb) cb(); }}, 200);
        }}, 120);
    }}

    document.addEventListener('touchstart', function(e) {{
        if (locked) return;
        var card = e.target.closest('.nl-card[data-ident]');
        if (!card) {{ tr=null; return; }}
        var t = e.touches[0];
        sx=t.clientX; sy0=t.clientY; swiping=false; moved=false; tr=card;
    }}, {{passive:true}});

    document.addEventListener('touchmove', function(e) {{
        if (!tr || locked) return;
        var t = e.touches[0];
        var dx = t.clientX-sx, dy = Math.abs(t.clientY-sy0);
        moved = true;
        if (!swiping && dy > 8) {{ tr.classList.remove('swiping'); tr=null; return; }}
        if (!swiping && dx < -18 && dy < 12) swiping=true;
        if (swiping) {{
            e.preventDefault();
            // Show clock when swiped enough
            if (dx < -30) tr.classList.add('swiping');
            else tr.classList.remove('swiping');
        }}
    }}, {{passive:false}});

    document.addEventListener('touchend', function(e) {{
        if (!tr || locked) return;
        var t = e.changedTouches[0];
        var dx = t.clientX-sx, dy = Math.abs(t.clientY-sy0);
        var card=tr; tr=null;
        card.classList.remove('swiping');
        if (swiping && dx < -60 && dy < 30) {{
            locked=true;
            swiping=false;
            flash(card, function() {{ locked=false; openWp(card.dataset.ident); }});
        }}
        swiping=false; moved=false;
    }}, {{passive:true}});
}})();

function openWp(ident) {{
    var card=document.querySelector('.nl-card[data-ident="'+ident+'"]');
    if(!card) return;
    _wpIdent=ident;
    document.getElementById('wp-fix-name').textContent=ident;
    var pAlt=card.dataset.plndAlt||'\u2014', pEt=card.dataset.plndEt||'\u2014', pFuel=card.dataset.plndFuel||'\u2014';
    document.getElementById('wp-p-alt').textContent=pAlt;
    document.getElementById('wp-p-et').textContent=typeof pEt==='string'&&pEt.length===4?pEt.slice(0,2)+':'+pEt.slice(2):pEt;
    document.getElementById('wp-p-fuel').textContent=pFuel;
    // Prefill actuals with existing values (or blank for fresh entry)
    var existAlt  = card.dataset.actAlt||'';
    var existEt   = card.dataset.actEt||'';
    var existFuel = card.dataset.actFuel||'';
    document.getElementById('wp-a-alt').value  = existAlt  ? existAlt  : (pAlt!=='\u2014'?pAlt:'');
    document.getElementById('wp-a-et').value   = existEt   ? existEt.slice(0,2)+':'+existEt.slice(2) : (pEt.length===4?pEt.slice(0,2)+':'+pEt.slice(2):'');
    document.getElementById('wp-a-fuel').value = existFuel ? existFuel : (pFuel!=='\u2014'?pFuel:'');
    var cards=Array.from(document.querySelectorAll('.nl-card[data-ident]'));
    var idx=cards.indexOf(card);
    var nextEt=(idx>=0&&idx<cards.length-1)?(cards[idx+1].dataset.plndEt||''):'';
    if(nextEt.length===4) nextEt=nextEt.slice(0,2)+':'+nextEt.slice(2);
    document.getElementById('wp-next-et').value=card.dataset.nextEt||nextEt;
    // Check fuel warning
    wpCheckFuelWarn();
    document.getElementById('wp-overlay').classList.add('visible');
    setTimeout(function(){{document.getElementById('wp-a-et').focus();}},80);
}}

function wpCheckFuelWarn() {{
    var warnEl = document.getElementById('wp-fuel-warn');
    if (!warnEl) return;
    var pFuelStr = document.getElementById('wp-p-fuel').textContent;
    var aFuelStr = document.getElementById('wp-a-fuel').value;
    var pFuel = parseInt(pFuelStr.replace(/[^0-9]/g,''));
    var aFuel = parseInt(aFuelStr.replace(/[^0-9]/g,''));
    warnEl.className = '';
    warnEl.textContent = '';
    if (!aFuelStr || isNaN(aFuel) || isNaN(pFuel) || pFuel <= 0) return;
    var diff = pFuel - aFuel;
    var pct  = diff / pFuel;
    if (aFuel <= 0 || pct >= 0.15) {{
        warnEl.className = 'warn-critical';
        warnEl.textContent = '\u26D4 INSUFFICIENT FUEL';
    }} else if (pct >= 0.05) {{
        warnEl.className = 'warn-caution';
        warnEl.textContent = '\u26A0 ACT FUEL ON BOARD SIGNIFICANTLY BELOW PLND FUEL ON BOARD';
    }}
}}

function closeWp() {{ document.getElementById('wp-overlay').classList.remove('visible'); _wpIdent=null; }}

function normFL(v) {{
    // 340 &#8594; FL340 (display), stored as "340"
    // 34000 &#8594; "340"
    v = v.replace(/[^0-9.]/g,'').trim();
    if(!v) return '';
    var n = parseFloat(v);
    if(n >= 1000) return String(Math.round(n/100));   // 34000 &#8594; 340, 18000 &#8594; 180
    return String(Math.round(n));                      // 340 &#8594; 340, 180 &#8594; 180
}}
function normFuel(v) {{
    // 19.4 &#8594; 19400, 194 &#8594; 19400 (if <1000 treat as hundreds? No &mdash; if <1000 ambiguous, use as-is)
    // Rule: if value has decimal &#8594; multiply by 1000 (19.4 &#8594; 19400)
    //       if value >= 100 and <= 999 &#8594; multiply by 100 (194 &#8594; 19400)
    //       else use raw integer
    v = v.trim();
    if(!v) return '';
    if(v.indexOf('.')!==-1) {{
        return String(Math.round(parseFloat(v)*1000));
    }}
    var n = parseInt(v);
    if(isNaN(n)) return '';
    if(n >= 100 && n <= 999) return String(n*100);
    return String(n);
}}

function saveWp() {{
    if(!_wpIdent) return;
    var card=document.querySelector('.nl-card[data-ident="'+_wpIdent+'"]');
    if(!card) {{ closeWp(); return; }}
    var aAlt  = normFL(document.getElementById('wp-a-alt').value);
    var aEt   = document.getElementById('wp-a-et').value.replace(':','').trim();
    var aFuel = normFuel(document.getElementById('wp-a-fuel').value);
    var nEt   = document.getElementById('wp-next-et').value.replace(':','').trim();
    if(aAlt)  card.dataset.actAlt  = aAlt;
    if(aEt)   card.dataset.actEt   = aEt;
    if(aFuel) card.dataset.actFuel = aFuel;
    if(nEt)   card.dataset.nextEt  = nEt;
    refreshRow(card);
    if(aFuel) {{
        var cards=Array.from(document.querySelectorAll('.nl-card[data-ident]'));
        var idx=cards.indexOf(card);
        var rem=parseInt(aFuel)||0;
        var prevCum=parseFloat(card.dataset.cumfuel)||0;
        for(var i=idx+1;i<cards.length;i++) {{
            if(cards[i].dataset.actFuel) break;
            var thisCum=parseFloat(cards[i].dataset.cumfuel)||0;
            rem-=(thisCum-prevCum); prevCum=thisCum;
            cards[i].dataset.plndFuel=Math.round(rem);
            var pF=cards[i].querySelector('.p-fuel'); if(pF) pF.textContent=Math.round(rem);
        }}
    }}
    try {{ localStorage.setItem(FLIGHT_KEY+'_wp_'+_wpIdent,JSON.stringify({{aAlt:aAlt,aEt:aEt,aFuel:aFuel,nEt:nEt}})); }} catch(e) {{}}
    closeWp();
    if(window.updateStatusBadge) updateStatusBadge();
}}

function refreshRow(card) {{
    var actEt=card.dataset.actEt||'', actFuel=card.dataset.actFuel||'', actAlt=card.dataset.actAlt||'';
    var aE=card.querySelector('.a-et'), aF=card.querySelector('.a-fuel'), aA=card.querySelector('.a-alt');
    if(aA) aA.textContent=actAlt||'';
    if(aE) aE.textContent=actEt?actEt.slice(0,2)+':'+actEt.slice(2):'';
    if(aF) aF.textContent=actFuel||'';
    setTrend(card,'.t-et',card.dataset.plndEt||'',actEt,true);
    setTrend(card,'.t-fuel',card.dataset.plndFuel||'',actFuel,false);
    nlStatusUpdate();
}}

function nlStatusUpdate() {{
    var cards=Array.from(document.querySelectorAll('#tab-navlog .nl-card[data-ident]'));
    if(!cards.length) return;
    var lastCard=cards[cards.length-1];
    var toff=(document.getElementById('nl-toff-inp')||{{}}).value||'';

    // &#9472;&#9472; ELDT &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    var eldtEl=document.getElementById('nl-eldt');
    if(eldtEl){{
        if(toff.length===4&&lastCard.dataset.plndEt&&lastCard.dataset.plndEt.length===4){{
            eldtEl.textContent=lastCard.dataset.plndEt.slice(0,2)+':'+lastCard.dataset.plndEt.slice(2);
        }}else if(PLAN_ELDT&&PLAN_ELDT.length===4){{
            eldtEl.textContent=PLAN_ELDT.slice(0,2)+':'+PLAN_ELDT.slice(2);
        }}
    }}

    // &#9472;&#9472; EST LDG FUEL &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    var ldgFuel=null;
    var ldgEl=document.getElementById('nl-ldgfuel');
    if(ldgEl){{
        var lastActCard=null;
        for(var i=cards.length-1;i>=0;i--){{ if(cards[i].dataset.actFuel){{lastActCard=cards[i];break;}} }}
        if(lastActCard){{
            // Extrapolate from last actual: remaining burn = dest cumfuel - lastActual cumfuel
            var destCum=parseFloat(lastCard.dataset.cumfuel)||0;
            var actCum=parseFloat(lastActCard.dataset.cumfuel)||0;
            var rem=destCum-actCum;
            ldgFuel=Math.round((parseInt(lastActCard.dataset.actFuel)||0)-rem);
            ldgEl.textContent=ldgFuel>0?ldgFuel.toLocaleString():'\u2014\u2014';
        }}else{{
            // No actuals &mdash; use entered block fuel minus total planned burn to dest
            var enteredFuel=parseInt((document.getElementById('nl-fuel-inp')||{{}}).value||
                                     (document.getElementById('input-fuel')||{{}}).value||'0')||0;
            var totalBurn=parseFloat(lastCard.dataset.cumfuel)||0;
            if(enteredFuel>0&&totalBurn>0){{
                ldgFuel=Math.round(enteredFuel-totalBurn);
                ldgEl.textContent=ldgFuel>0?ldgFuel.toLocaleString():'\u2014\u2014';
            }}else if(PLAN_LDG_FUEL>0){{
                ldgFuel=PLAN_LDG_FUEL;
                ldgEl.textContent=PLAN_LDG_FUEL.toLocaleString();
            }}
        }}
    }}

    // &#9472;&#9472; EST LDG WEIGHT &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    var wgtEl=document.getElementById('nl-ldgwgt');
    if(wgtEl&&PLAN_ZFW>0){{
        var lf=ldgFuel!==null?ldgFuel:PLAN_LDG_FUEL;
        if(lf>0){{
            var lgw=Math.round(PLAN_ZFW+lf);
            var wgtStr=lgw.toLocaleString();
            if(PLAN_MAX_LDW>0&&lgw>PLAN_MAX_LDW) wgtStr+='\u00a0\u26D4';
            wgtEl.textContent=wgtStr;
        }}
    }}
}}

function _nlToast(msg) {{
    var t = document.getElementById('nl-toast');
    if (!t) {{
        t = document.createElement('div');
        t.id = 'nl-toast';
        t.style.cssText = 'position:fixed;bottom:90px;left:50%;transform:translateX(-50%);'
            + 'background:rgba(180,120,0,0.92);color:#fff3cc;font-size:13px;font-weight:600;'
            + 'padding:10px 20px;border-radius:20px;z-index:2000;pointer-events:none;'
            + 'letter-spacing:.3px;white-space:nowrap;font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;'
            + 'box-shadow:0 4px 16px rgba(0,0,0,0.4);';
        document.body.appendChild(t);
    }}
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(t._hide);
    t._hide = setTimeout(function() {{ t.style.opacity = '0'; }}, 2200);
}}

function nlToggleDetail(detailId, ident) {{
    var d = document.getElementById(detailId);
    if (!d) return;
    var open = d.style.display !== 'none' && d.style.display !== '';
    d.style.display = open ? 'none' : 'block';
}}

function nlFir(gid) {{
    var detail = document.getElementById(gid + '-detail');
    var chev   = document.getElementById(gid + 'c');
    if (!detail) return;
    var open = detail.style.display === 'block';
    detail.style.display = open ? 'none' : 'block';
    if (chev) chev.style.transform = open ? '' : 'rotate(180deg)';
}}

function setTrend(row,sel,plnd,act,isTime) {{
    var c=row.querySelector(sel); if(!c) return;
    if(!act||!plnd||plnd==='\u2014'||plnd==='') {{ c.textContent=''; c.className=c.className.replace(/ ?pos| ?neg/g,''); return; }}
    var diff=isTime?hhmm2mins(act)-hhmm2mins(plnd):(parseInt(act)||0)-(parseInt(plnd)||0);
    if(diff===0) {{ c.textContent=''; c.className=c.className.replace(/ ?pos| ?neg/g,''); return; }}
    // Time: earlier (diff<0) = good = green ↓ | later (diff>0) = bad = red ↑
    // Fuel: higher (diff>0) = good = green ↑ | lower (diff<0) = bad = red ↓
    var better = isTime ? diff<0 : diff>0;
    var arrow = diff>0 ? ' ↑' : ' ↓';
    if(isTime) {{ var a=Math.abs(diff); c.textContent=(diff>0?'+':'-')+String(Math.floor(a/60)).padStart(2,'0')+':'+String(a%60).padStart(2,'0')+arrow; }}
    else {{ c.textContent=(diff>0?'+':'')+Math.round(diff)+arrow; }}
    c.className=c.className.replace(/ ?pos| ?neg/g,'')+(better?' pos':' neg');
}}

function toggleNavlog() {{
    var s=document.getElementById('navlog-section'),btn=document.querySelector('.navlog-toggle');
    var v=(s.style.display!=='none'); s.style.display=v?'none':'block';
    if(btn){{if(v)btn.classList.add('collapsed');else btn.classList.remove('collapsed');}}
    try{{localStorage.setItem('collapse_navlog',v?'1':'0');}}catch(e){{}}
}}

document.addEventListener('keydown',function(e){{
    if(e.key==='Enter'){{
        if(document.getElementById('wp-overlay').classList.contains('visible')) saveWp();
        else if(document.getElementById('entry-overlay').classList.contains('visible')) applyEntryValues();
    }}
    if(e.key==='Escape') closeWp();
}});

window.addEventListener('load',function(){{
    // Set spacer height so navlog content clears the fixed bars
    function _nlSetSpacer() {{
        var wrap = document.getElementById('nl-sticky-wrap');
        var spacer = document.getElementById('nl-bar-spacer');
        if(wrap && spacer) spacer.style.height = wrap.offsetHeight + 'px';
    }}
    _nlSetSpacer();
    setTimeout(_nlSetSpacer, 200);
    window.nlSetSpacer = _nlSetSpacer;
    try{{
        var t=localStorage.getItem(FLIGHT_KEY+'_toff'),fu=localStorage.getItem(FLIGHT_KEY+'_fuel');
        if(t) document.getElementById('input-toff').value=t;
        if(fu) document.getElementById('input-fuel').value=fu;
        if(t||fu) applyEntryValues();
        // Restore ATOT label if T/O time was previously saved
        if(t) {{ var el=document.getElementById('svg-lbl-etot'); if(el) el.textContent='ATOT'; }}
    }}catch(e){{}}
    document.querySelectorAll('.nl-card[data-ident]').forEach(function(card){{
        try{{
            var d=JSON.parse(localStorage.getItem(FLIGHT_KEY+'_wp_'+card.dataset.ident)||'null');
            if(d){{
                if(d.aAlt) card.dataset.actAlt=d.aAlt;
                if(d.aEt)  card.dataset.actEt=d.aEt;
                if(d.aFuel) card.dataset.actFuel=d.aFuel;
                if(d.nEt)  card.dataset.nextEt=d.nEt;
                refreshRow(card);
            }}
        }}catch(e){{}}
    }});
    nlStatusUpdate();
}});

function openFinalSubmit() {{
    document.getElementById('fs-overlay').style.display='flex';
    document.getElementById('fs-remarks').value='';
    document.getElementById('fs-incomplete').checked=false;
}}
function closeFinalSubmit() {{
    document.getElementById('fs-overlay').style.display='none';
}}
function finalSubmit() {{
    var remarks    = document.getElementById('fs-remarks').value.trim();
    var incomplete = document.getElementById('fs-incomplete').checked;

    var toff = (document.getElementById('nl-toff-inp')||{{}}).value ||
               (document.getElementById('input-toff')||{{}}).value || '';
    var fuel = (document.getElementById('nl-fuel-inp')||{{}}).value ||
               (document.getElementById('input-fuel')||{{}}).value || '';
    var eldt    = (document.getElementById('nl-eldt')||{{}}).textContent   || '';
    var ldgFuel = (document.getElementById('nl-ldgfuel')||{{}}).textContent || '';
    var ldgWgt  = (document.getElementById('nl-ldgwgt')||{{}}).textContent  || '';

    var waypoints = [];
    document.querySelectorAll('#tab-navlog .nl-card[data-ident]').forEach(function(card) {{
        waypoints.push({{
            ident:    card.dataset.ident,
            plndEt:   card.dataset.plndEt   || '',
            plndFuel: card.dataset.plndFuel  || '',
            plndAlt:  card.dataset.plndAlt   || '',
            actEt:    card.dataset.actEt     || '',
            actFuel:  card.dataset.actFuel   || '',
            actAlt:   card.dataset.actAlt    || ''
        }});
    }});

    var now   = new Date();
    var ts    = now.toISOString().replace('T',' ').slice(0,19) + 'Z';
    var subId = 'NL-' + now.getTime().toString(36).toUpperCase();

    var snapshot = {{
        subId: subId, timestamp: ts,
        remarks: remarks, incomplete: incomplete,
        toff: toff, fuel: fuel,
        eldt: eldt, ldgFuel: ldgFuel, ldgWgt: ldgWgt,
        waypoints: waypoints
    }};

    try {{ localStorage.setItem(FLIGHT_KEY + '_final', JSON.stringify(snapshot)); }} catch(e) {{}}

    closeFinalSubmit();

    var btn = document.getElementById('nl-final-submit-btn');
    if (btn) {{
        var origBg = btn.style.background, origHtml = btn.innerHTML;
        btn.style.background = 'linear-gradient(90deg,#145a2a,#1a7a38)';
        btn.innerHTML = '&#10003;<br>SAVED';
        setTimeout(function() {{ btn.style.background=origBg; btn.innerHTML=origHtml; }}, 2500);
    }}
    _nlToast('Navlog submitted \u2014 ' + subId);
}}
</script>
"""
    html += navlog_css_js

    # &#9472;&#9472; FINAL SUBMIT MODAL &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += """
<div id='fs-overlay' style='display:none;position:fixed;top:0;left:0;right:0;bottom:0;
  z-index:1200;background:rgba(8,24,38,0.82);align-items:center;justify-content:center;
  padding:20px;box-sizing:border-box;'>
  <div style='background:linear-gradient(160deg,#1a3f56 0%,#1e4d6b 100%);
    border:1px solid rgba(90,160,210,0.25);border-radius:14px;
    width:100%;max-width:420px;padding:32px 28px 26px;box-sizing:border-box;
    box-shadow:0 16px 48px rgba(0,0,0,0.6);font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;'>
    <div style='text-align:center;margin-bottom:6px;font-size:22px;font-weight:300;color:#e8f6ff;letter-spacing:0.2px;'>Final submit</div>
    <div style='text-align:center;font-size:11px;font-weight:700;color:#4a8aaa;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:22px;'>ALL OFP DATA WILL BE SUBMITTED</div>
    <textarea id='fs-remarks' placeholder='REMARKS:' rows='4'
      style='width:100%;box-sizing:border-box;background:rgba(255,255,255,0.07);
      border:none;border-bottom:1.5px solid rgba(90,160,210,0.5);border-radius:0;
      color:#e8f6ff;font-size:15px;padding:10px 12px;resize:none;outline:none;
      font-family:inherit;letter-spacing:0.3px;margin-bottom:16px;'></textarea>
    <label style='display:flex;align-items:center;gap:10px;cursor:pointer;margin-bottom:20px;'>
      <input type='checkbox' id='fs-incomplete'
        style='width:18px;height:18px;accent-color:#4da8da;cursor:pointer;flex-shrink:0;'>
      <span style='font-size:14px;color:#c8dff0;'>Incomplete</span>
    </label>
    <div style='text-align:center;font-size:13px;font-weight:600;color:#c8dff0;
      line-height:1.5;margin-bottom:24px;padding:0 4px;'>
      I confirm that this OFP is accurate, complete and in compliance with<br>
      the applicable sections of the operations manual.
    </div>
    <div style='display:flex;gap:12px;'>
      <button onclick='closeFinalSubmit()' style='flex:1;background:transparent;
        color:#e8f6ff;border:1.5px solid rgba(200,220,240,0.5);border-radius:7px;
        padding:13px;font-size:13px;font-weight:700;letter-spacing:1px;
        text-transform:uppercase;cursor:pointer;font-family:inherit;'>CANCEL</button>
      <button onclick='finalSubmit()' style='flex:1;background:#29b6e8;
        color:#fff;border:none;border-radius:7px;
        padding:13px;font-size:13px;font-weight:700;letter-spacing:1px;
        text-transform:uppercase;cursor:pointer;font-family:inherit;'>SUBMIT</button>
    </div>
  </div>
</div>
"""



    # &#9472;&#9472; Close overview content div before tab overlays &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "</div>"  # content

    # &#9472;&#9472; NAVLOG TAB OVERLAY &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _tab_overlay_style = ("display:none;position:fixed;top:0;left:0;right:0;bottom:0;"
                          "z-index:600;padding-top:calc(var(--topbar-h,88px) + var(--banner-h,0px));"
                          "padding-bottom:80px;"
                          "background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);"
                          "background-attachment:fixed;"
                          "overflow-y:auto;-webkit-overflow-scrolling:touch;")
    html += f"<div id='tab-navlog' style='{_tab_overlay_style}'>"
    html += "<div class='overlay-inner' id='nl-overlay-inner'>"

    # Fixed navlog header bars &mdash; pinned just below the topbar, always accessible
    html += "<div id='nl-sticky-wrap' style='position:fixed;left:0;right:0;top:var(--topbar-h,88px);z-index:601;'>"
    html += "<div style='max-width:900px;margin:0 auto;'>"

    # status bar
    _nl_rls = data['ofp'].get('time', '1')
    html += (
        "<div id='nl-status'>"
        "<div class='nl-sb-col'><span class='nl-sb-lbl'>ALL WEIGHTS<br>IN LB</span></div>"
        "<div class='nl-sb-col'><span class='nl-sb-lbl'>ELDT</span><span class='nl-sb-val' id='nl-eldt'>\u2014\u2014</span></div>"
        "<div class='nl-sb-col'><span class='nl-sb-lbl'>EST LDG FUEL</span><span class='nl-sb-val' id='nl-ldgfuel'>\u2014\u2014</span></div>"
        "<div class='nl-sb-col'><span class='nl-sb-lbl'>EST LDG WEIGHT</span><span class='nl-sb-val' id='nl-ldgwgt'>\u2014\u2014</span></div>"
        "<div style='display:flex;align-items:center;gap:8px;padding:10px 14px;flex-shrink:0;'>"
        "<button style='width:34px;height:34px;background:rgba(90,174,239,0.1);border:1px solid rgba(90,174,239,0.25);border-radius:5px;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;'>"
        "<svg width='15' height='15' viewBox='0 0 15 15' fill='none'><rect x='1' y='1' width='5' height='5' rx='1' stroke='#7ad8fd' stroke-width='1.4'/><rect x='9' y='1' width='5' height='5' rx='1' stroke='#7ad8fd' stroke-width='1.4'/><rect x='1' y='9' width='5' height='5' rx='1' stroke='#7ad8fd' stroke-width='1.4'/><rect x='9' y='9' width='5' height='5' rx='1' stroke='#7ad8fd' stroke-width='1.4'/></svg>"
        "</button>"
        "<button style='width:34px;height:34px;background:rgba(90,174,239,0.1);border:1px solid rgba(90,174,239,0.25);border-radius:5px;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;'>"
        "<svg width='15' height='15' viewBox='0 0 15 15' fill='none'><path d='M7.5 2v11M3.5 6l4-4 4 4' stroke='#7ad8fd' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/></svg>"
        "</button>"
        "<button onclick='openFinalSubmit()' style='background:linear-gradient(90deg,#1a6a9a,#1e7db8);border:none;border-radius:5px;padding:7px 13px;color:#fff;font-size:11px;font-weight:800;letter-spacing:.4px;cursor:pointer;line-height:1.25;white-space:nowrap;font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;' id='nl-final-submit-btn'>"
        "FINAL<br>SUBMIT"
        "</button>"
        "</div>"
        "</div>"
    )

    # takeoff time + fuel bar
    html += (
        f"<div id='nl-toff-bar'>"
        f"<span id='nl-toff-lbl'>T/O Time</span>"
        f"<input id='nl-toff-inp' type='text' inputmode='numeric' maxlength='5'"
        f" placeholder='{sched_off}'"
        f" oninput=\"document.getElementById('input-toff').value=this.value;document.getElementById('navlog-toff').value=this.value\">"
        f"<input type='hidden' id='navlog-toff'>"
        f"<span id='nl-fuel-lbl'>T/O Fuel</span>"
        f"<input id='nl-fuel-inp' type='text' inputmode='numeric' maxlength='7'"
        f" placeholder='{plan_ramp}'"
        f" oninput=\"document.getElementById('input-fuel').value=this.value\">"
        f"<button id='nl-apply-btn' onclick='applyEntryValues()'>Apply</button>"
        f"<button id='nl-reset-btn' onclick='resetAllValues()'>&#8635; Reset</button>"
        f"</div>"
    )
    html += "</div>"  # /max-width centering inner
    html += "</div>"  # /nl-sticky-wrap (fixed bars)
    # Spacer &mdash; JS sets its height to match the fixed bars so content clears them
    html += "<div id='nl-bar-spacer'></div>"

    # OFP RLS banner (signed) and read-only bar (unsigned) &mdash; content width, in flow
    html += (
        f"<div style='padding:10px 12px 0;'>"
        # Green signed banner &mdash; hidden until OFP accepted
        f"<div id='nl-rls-banner' style='display:none;background:#32d96a;border-radius:7px;"
        f"padding:9px 14px;align-items:center;gap:10px;margin-bottom:4px;'>"
        f"<span style='color:#0a2e14;font-size:15px;font-weight:700;'>&#10003;</span>"
        f"<span style='font-size:12px;font-weight:700;color:#0a2e14;letter-spacing:.2px;"
        f"font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;'>"
        f"OFP RLS {_nl_rls} ACCEPTED</span>"
        f"</div>"
        # Yellow read-only bar &mdash; shown until signed
        f"<div id='nl-unsigned-bar' style='display:flex;align-items:center;gap:10px;"
        f"background:rgba(180,120,0,0.18);border:1px solid rgba(220,160,0,0.3);border-radius:7px;"
        f"padding:8px 14px;margin-bottom:4px;'>"
        f"<span style='font-size:14px;color:#ffd060;'>&#9888;</span>"
        f"<span style='font-size:12px;font-weight:600;color:#ffd060;letter-spacing:.2px;"
        f"font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;'>"
        f"OFP RLS {_nl_rls} not yet accepted</span>"
        f"</div>"
        f"</div>"
    )

    # &#9472;&#9472; NAVLOG: div-card layout &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div id='nl-table-wrap'>\n"

    # Column header rows &mdash; titles row + sub-labels row, both using same grid as cards
    html += (
        # Row 1: group titles (spanning their sub-columns)
        "<div class='nlh-row'>"
        "<div class='nlh-fix-spc'></div>"
        "<div class='nlh-ma-spc'>MA</div>"
        "<div class='nlh-fl-title'>FLIGHT LEVEL</div>"
        "<div class='nlh-ov-title'>OVERFLIGHT</div>"
        "<div class='nlh-fob-title'>FUEL ON BOARD</div>"
        "<div class='nlh-bar-spc'></div>"
        "</div>"
        # Row 2: sub-labels (one per grid column)
        "<div class='nlh-subs-row'>"
        "<div></div>"  # fix spacer
        "<div></div>"  # ma spacer
        "<div class='nlh-sub'>PLND</div>"
        "<div class='nlh-sub'>ACT/EST</div>"
        "<div class='nlh-sub'>PLND</div>"
        "<div class='nlh-sub'>ACT/EST</div>"
        "<div class='nlh-sub'>TREND</div>"
        "<div class='nlh-sub'>PLND</div>"
        "<div class='nlh-sub'>ACT/EST</div>"
        "<div class='nlh-sub'>TREND</div>"
        "<div></div>"  # bar spacer
        "</div>\n"
    )

    def _nl_card(ident, track_mag, wind, alt, et_plnd, airway='', heading_mag='', mach='', ind_true='', row_class='', cumfuel_data=0, fix_data=None):
        et_fmt = et_plnd[:2]+':'+et_plnd[2:] if (et_plnd and len(et_plnd)==4 and et_plnd.isdigit()) else (et_plnd or '')
        try:
            alt_fl = str(int(round(float(alt)/100))) if alt and str(alt) not in ('---','') and float(alt) >= 100 else (alt or '')
        except Exception:
            alt_fl = alt or ''
        wind_raw = wind if wind and wind.strip('/') and wind.strip() not in ('/', '/', '---/---') else ''

        # Pull all real values from fix_data
        fd = fix_data or {}

        mora_raw = fd.get('mora', '') or ''
        ma_val = str(mora_raw).strip() if mora_raw and str(mora_raw).strip() not in ('', '---', '0') else '-'
        is_special = row_class in ('nl-toc', 'nl-tod', 'nl-dest')

        def fv(v):
            """Return value or '-' if empty/---"""
            s = str(v).strip() if v else ''
            return s if s and s not in ('---', '0', '/', '-/-', '---/---') else '-'

        airway_d    = fv(fd.get('airway','') or airway)
        heading_d   = fv(fd.get('heading_mag','') or heading_mag)
        tc_d        = fv(fd.get('track_true',''))
        mc_d        = fv(fd.get('mag_course',''))
        dist_d      = fv(fd.get('dist',''))
        if dist_d != '-':
            try: dist_d = str(round(float(dist_d)))
            except Exception: pass
        dtg_d       = fv(fd.get('dist_to_go',''))
        if dtg_d != '-':
            try: dtg_d = str(round(float(dtg_d)))
            except Exception: pass
        # TAS / GS from ind_true field (IAS/TAS format)
        it = fd.get('ind_true','') or ind_true or ''
        tas_d, gs_d = '-', '-'
        if it and '/' in it:
            parts = it.split('/')
            tas_d = fv(parts[0]) if parts[0] not in ('','---') else '-'
            gs_d  = fv(parts[1]) if len(parts)>1 and parts[1] not in ('','---') else '-'
        mach_d      = fv(fd.get('mach','') or mach)
        trp_d       = fv(fd.get('trp',''))
        ma_d        = ma_val
        temp_raw    = fd.get('temperature','')
        temp_d      = fv(temp_raw)
        if temp_d != '-':
            try:
                temp_d = str(int(float(temp_d)))
            except Exception:
                pass
        wind_d      = fv(wind_raw)
        # SEGF: segment fuel used
        segf_d      = fv(fd.get('seg_fuel',''))
        # SEGT: segment time in MM:SS or HH:MM
        segt_raw    = fd.get('seg_time','')
        if segt_raw and str(segt_raw).strip() not in ('','---','0'):
            try:
                s = int(segt_raw)
                segt_d = f"{s//60:02d}:{s%60:02d}"
            except Exception:
                segt_d = fv(segt_raw)
        else:
            segt_d = '-'

        card_cls = f"nl-card{' nl-special' if is_special else ''}"
        detail_id = f"nld-{ident.replace(' ','_').replace('(','').replace(')','').replace('/','_')}"

        detail_html = (
            f"<div class='nl-detail' id='{detail_id}' style='display:none;'>"
            f"<div class='nl-detail-grid'>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>AWY</div><div class='nl-dg-val'>{airway_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>MH</div><div class='nl-dg-val'>{heading_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>TC</div><div class='nl-dg-val'>{tc_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>MC</div><div class='nl-dg-val'>{mc_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>DIST</div><div class='nl-dg-val'>{dist_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>DTG</div><div class='nl-dg-val'>{dtg_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>TAS</div><div class='nl-dg-val'>{tas_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>GS</div><div class='nl-dg-val'>{gs_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>MACH</div><div class='nl-dg-val'>{mach_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>TRP</div><div class='nl-dg-val'>{trp_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>MT</div><div class='nl-dg-val'>{fv(fd.get('track_mag',''))}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>MORA</div><div class='nl-dg-val'>{ma_val}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>TEMP</div><div class='nl-dg-val'>{temp_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>WIND</div><div class='nl-dg-val'>{wind_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>SEGF</div><div class='nl-dg-val'>{segf_d}</div></div>"
            f"<div class='nl-dg-item'><div class='nl-dg-lbl'>SEGT</div><div class='nl-dg-val'>{segt_d}</div></div>"
            f"</div>"
            f"</div>"
        )

        return (
            f"<div class='{card_cls}' data-ident='{ident}' data-cumfuel='{cumfuel_data}'"
            f" data-plnd-alt='{alt_fl}' data-plnd-et='{et_plnd}' data-plnd-fuel=''>"
            f"<div class='nl-row' onclick='nlToggleDetail(\"{detail_id}\",\"{ident}\")'>"
            f"<div class='nl-fix'>{ident}</div>"
            f"<div class='nl-ma'>{ma_val}</div>"
            f"<div class='nl-c nl-plnd p-alt'>{alt_fl or '-'}</div>"
            f"<div class='nl-c nl-act a-alt'></div>"
            f"<div class='nl-c nl-plnd p-et'>{et_fmt or '-'}</div>"
            f"<div class='nl-c nl-act a-et'></div>"
            f"<div class='nl-trend t-et'></div>"
            f"<div class='nl-c nl-plnd p-fuel'></div>"
            f"<div class='nl-c nl-act a-fuel'></div>"
            f"<div class='nl-trend t-fuel'></div>"
            f"<div class='nl-swipe-bar'><span class='nl-swipe-clock'>&#9200;</span></div>"
            f"</div>"
            f"{detail_html}"
            f"</div>\n"
        )

    dest_icao_tbl   = a['destination']['icao']
    dest_et_elapsed = f.get('t_enroute', '\u2014\u2014') or '\u2014\u2014'
    _dest_row_added = False
    _last_fir       = None
    _fir_idx        = 0

    for fix in navlog_fixes:
        is_tod = fix.get('is_tod')
        is_toc = fix.get('is_toc')
        rc = 'nl-toc' if is_toc else ('nl-tod' if is_tod else '')

        # FIR label &mdash; just a small text divider, no card
        fir_code = (fix.get('fir') or '').strip().upper()
        fir_name = (fix.get('fir_name') or '').strip()
        if fir_code and fir_code != _last_fir and not is_toc:
            _last_fir = fir_code
            _fir_idx += 1
            gid = f"nlf{_fir_idx}"
            label = f"({fir_code}) {fir_name}" if fir_name else fir_code
            html += (
                f"<div class='nl-fir-label' id='{gid}' onclick='nlFir(\"{gid}\")'>"
                f"{label}"
                f"<span class='nl-fir-chev' id='{gid}c'>&#8964;</span>"
                f"</div>\n"
                f"<div id='{gid}-detail' style='display:none;padding:8px 12px 4px;'>"
                f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;'>"
                f"<div><div style='font-size:9px;color:#4e7a96;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px;'>VHF 1</div>"
                f"<input type='text' placeholder='— MHz' maxlength='10' style='width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(90,174,239,0.2);border-radius:4px;color:#eaf6ff;font-size:13px;padding:5px 8px;outline:none;font-family:inherit;'></div>"
                f"<div><div style='font-size:9px;color:#4e7a96;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px;'>VHF 2</div>"
                f"<input type='text' placeholder='— MHz' maxlength='10' style='width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(90,174,239,0.2);border-radius:4px;color:#eaf6ff;font-size:13px;padding:5px 8px;outline:none;font-family:inherit;'></div>"
                f"<div><div style='font-size:9px;color:#4e7a96;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px;'>CPDLC</div>"
                f"<input type='text' placeholder='—' maxlength='20' style='width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(90,174,239,0.2);border-radius:4px;color:#eaf6ff;font-size:13px;padding:5px 8px;outline:none;font-family:inherit;'></div>"
                f"<div><div style='font-size:9px;color:#4e7a96;text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px;'>Notes</div>"
                f"<input type='text' placeholder='—' maxlength='40' style='width:100%;background:rgba(255,255,255,0.06);border:1px solid rgba(90,174,239,0.2);border-radius:4px;color:#eaf6ff;font-size:13px;padding:5px 8px;outline:none;font-family:inherit;'></div>"
                f"</div></div>\n"
            )

        html += _nl_card(
            ident=fix['ident'], track_mag=fix.get('track_mag',''),
            wind=fix.get('wind',''), alt=fix.get('altitude',''),
            et_plnd=fix['et_hhmm'], airway=fix.get('airway',''),
            heading_mag=fix.get('heading_mag',''), mach=fix.get('mach',''),
            ind_true=fix.get('ind_true',''), row_class=rc,
            cumfuel_data=fix['cum_fuel_used'], fix_data=fix
        )
        if is_tod and not _dest_row_added:
            _dest_row_added = True
            html += _nl_card(ident=dest_icao_tbl, track_mag='', wind='', alt='0',
                             et_plnd=dest_et_elapsed, row_class='nl-dest', cumfuel_data=dest_fuel_used)
            break

    if not _dest_row_added:
        html += _nl_card(ident=dest_icao_tbl, track_mag='', wind='', alt='0',
                         et_plnd=dest_et_elapsed, row_class='nl-dest', cumfuel_data=dest_fuel_used)

    html += "</div>\n"  # nl-table-wrap
    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-navlog

    # Images, Weather, NOTAMs &mdash; rendered as full-screen tab overlays
    if data.get('images'):
        html += f"<div id='tab-maps' style='{_tab_overlay_style}'>"
        html += "<div class='overlay-inner'>"
        html += "<div style='padding:12px;'>"
        for img in data['images']:
            html += "<div class='section' style='margin-bottom:10px;'>"
            html += f"  <div style='padding:10px 16px;font-size:11px;color:#6ab4d4;letter-spacing:0.5px;text-transform:uppercase;border-bottom:1px solid rgba(90,174,239,0.1);'>{img['name']}</div>"
            html += f"  <div class='section-body'><img src='{img['link']}' alt='{img['name']}' loading='lazy' style='max-width:100%;border-radius:4px;'></div>"
            html += "</div>"
        html += "</div>"   # padding div
        html += "</div>"   # overlay-inner
        html += "</div>"   # tab-maps

    # Weather overlay
    weather_html = data.get('weather_html', '')
    if weather_html:
        html += f"<div id='tab-weather' style='{_tab_overlay_style}'>"
        html += "<div class='overlay-inner'>"
        html += "<div style='padding:12px;'>"
        html += weather_html
        html += "</div>"   # padding div
        html += "</div>"   # overlay-inner
        html += "</div>"   # tab-weather

    # NOTAMs overlay
    notams_html = data.get('notams_html', '')
    if notams_html:
        html += f"<div id='tab-notams' style='{_tab_overlay_style}'>"
        html += "<div class='overlay-inner'>"
        html += "<div style='padding:12px;'>"
        html += "  <div id='pinned-notams-bar' class='notam-pinned-bar' style='display:none;'>"
        html += "    <div class='notam-pinned-title'>&#128204; PINNED NOTAMs</div>"
        html += "    <div class='notam-pinned-body' id='pinned-notams-list'></div>"
        html += "  </div>"
        html += notams_html
        html += "</div>"   # padding div
        html += "</div>"   # overlay-inner
        html += "</div>"   # tab-notams

    # &#9472;&#9472; BOTTOM NAV BAR (Aviobook style) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='bottom-nav'>"
    for icon, label, section, active, link in [
        ("&#9992;",        "OFP",        "ofp",        False, None),
        ("&#9741;",  "BRIEFING",   "briefing",   True,  None),
        ("&#9992;",  "AIRPORTS",   "airports",   False, None),
        ("&#9999;",  "NOTES",      "notes",      False, None),
        ("&#9992;",  "FOREFLIGHT", "fdpro",      False, _ff_url),
        ("&#128218;","LIBRARY",    "library",    False, None),
        ("&#9733;",  "FORMS",      "forms",      False, None),
        ("&#9776;",  "CHECKLISTS", "checklists", False, None),
        ("&#9881;",  "TOOLS",      "tools",      False, None),
    ]:
        cls = " active" if active else ""
        if link:
            html += (f"<div class='bottom-nav-item{cls}' id='bnav-{section}' "
                     f"onclick=\"window.location.href='{link}'\" "
                     f"style='cursor:pointer;'>")
            html += f"  <span class='bottom-nav-icon'>{icon}</span>"
            html += f"  <span>{label}</span>"
            html += "</div>"
        else:
            html += f"<div class='bottom-nav-item{cls}' id='bnav-{section}' onclick='switchSection(\"{section}\")'>"
            html += f"  <span class='bottom-nav-icon'>{icon}</span>"
            html += f"  <span>{label}</span>"
            html += "</div>"
    html += "</div>"

    # &#9472;&#9472; NOTAM PIN JS &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += """
<script>
var pinnedNotams = {};
try { pinnedNotams = JSON.parse(localStorage.getItem('av_pinned_notams') || '{}'); } catch(e) {}

function togglePin(nid, entryEl) {
    if (pinnedNotams[nid]) {
        delete pinnedNotams[nid];
        entryEl.classList.remove('pinned');
        var btn = entryEl.querySelector('.notam-pin-btn');
        if (btn) { btn.classList.remove('pinned-active'); btn.textContent = '\u25CB'; }
    } else {
        // Store plain text content only — never raw HTML — to prevent XSS via innerHTML round-trip
        var metaEl = entryEl.querySelector('.notam-meta');
        var bodyEl = entryEl.querySelector('.notam-body');
        pinnedNotams[nid] = {
            id:   nid,
            meta: metaEl ? metaEl.textContent : '',
            body: bodyEl ? bodyEl.textContent : ''
        };
        entryEl.classList.add('pinned');
        var btn = entryEl.querySelector('.notam-pin-btn');
        if (btn) { btn.classList.add('pinned-active'); btn.textContent = '\u25CB'; }
    }
    try { localStorage.setItem('av_pinned_notams', JSON.stringify(pinnedNotams)); } catch(e) {}
    renderPinnedBar();
}

function unpinFromBar(nid) {
    delete pinnedNotams[nid];
    try { localStorage.setItem('av_pinned_notams', JSON.stringify(pinnedNotams)); } catch(e) {}
    // Also update the original entry in the list
    var orig = document.querySelector('.notam-entry[data-nid="' + nid + '"]');
    if (orig) {
        orig.classList.remove('pinned');
        var btn = orig.querySelector('.notam-pin-btn');
        if (btn) { btn.classList.remove('pinned-active'); btn.textContent = '\u25CB'; }
    }
    renderPinnedBar();
}

function renderPinnedBar() {
    var bar = document.getElementById('pinned-notams-bar');
    var list = document.getElementById('pinned-notams-list');
    if (!bar || !list) return;
    var keys = Object.keys(pinnedNotams);
    if (keys.length === 0) { bar.style.display = 'none'; return; }
    bar.style.display = '';
    list.innerHTML = '';
    keys.forEach(function(nid) {
        var d = pinnedNotams[nid];
        // Legacy guard: if stored value is a string (old outerHTML format), skip re-rendering it
        if (typeof d === 'string') { return; }

        // Build card entirely from safe text content — no innerHTML of untrusted data
        var wrap = document.createElement('div');
        wrap.className = 'notam-entry pinned';
        wrap.style.padding = '4px 12px';

        var hdr = document.createElement('div');
        hdr.className = 'notam-entry-hdr';

        var idSpan = document.createElement('span');
        idSpan.className = 'notam-id';
        idSpan.textContent = d.id || nid;

        var metaSpan = document.createElement('span');
        metaSpan.className = 'notam-meta';
        metaSpan.textContent = d.meta || '';

        var unpinBtn = document.createElement('button');
        unpinBtn.className = 'notam-pin-btn pinned-active';
        unpinBtn.title = 'Unpin NOTAM';
        unpinBtn.textContent = '\u25CB';
        (function(capturedNid) {
            unpinBtn.addEventListener('click', function() { unpinFromBar(capturedNid); });
        })(nid);

        hdr.appendChild(idSpan);
        hdr.appendChild(metaSpan);
        hdr.appendChild(unpinBtn);

        var body = document.createElement('div');
        body.className = 'notam-body';
        body.textContent = d.body || '';

        wrap.appendChild(hdr);
        wrap.appendChild(body);
        list.appendChild(wrap);
    });
}

// Restore pin state on load
window.addEventListener('load', function() {
    // Migrate any legacy string-format pins (old outerHTML) — drop them, they can't be safely re-used
    var migrated = false;
    Object.keys(pinnedNotams).forEach(function(nid) {
        if (typeof pinnedNotams[nid] === 'string') {
            delete pinnedNotams[nid];
            migrated = true;
        }
    });
    if (migrated) {
        try { localStorage.setItem('av_pinned_notams', JSON.stringify(pinnedNotams)); } catch(e) {}
    }
    document.querySelectorAll('.notam-entry[data-nid]').forEach(function(el) {
        var nid = el.dataset.nid;
        if (pinnedNotams[nid]) {
            el.classList.add('pinned');
            var btn = el.querySelector('.notam-pin-btn');
            if (btn) { btn.classList.add('pinned-active'); btn.textContent = '\u25CB'; }
        }
    });
    renderPinnedBar();
});

// &#9472;&#9472; Sign overlay JS &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
var _signed={};
var _sigDrawing={ofp:false,ffd:false};
var _sigHasData={ofp:false,ffd:false};

// &#9472;&#9472; Signature canvas init &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
function _initCanvas(id) {
  var canvas = document.getElementById(id+'-sig-canvas');
  if (!canvas) return;
  var wrap = document.getElementById(id+'-canvas-wrap');
  var ph = document.getElementById(id+'-sig-placeholder');
  var st = document.getElementById(id+'-sig-status');

  // Size canvas to actual pixel width
  function resizeCanvas() {
    var dpr = window.devicePixelRatio || 1;
    canvas.width  = canvas.offsetWidth  * dpr;
    canvas.height = canvas.offsetHeight * dpr;
    var ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.strokeStyle = '#7ac4e8';
    ctx.lineWidth   = 2.2;
    ctx.lineCap     = 'round';
    ctx.lineJoin    = 'round';
  }
  resizeCanvas();

  function getPos(e) {
    var r = canvas.getBoundingClientRect();
    var src = e.touches ? e.touches[0] : e;
    return { x: src.clientX - r.left, y: src.clientY - r.top };
  }

  var ctx = canvas.getContext('2d');
  var drawing = false, lastPos = null;

  function startDraw(e) {
    drawing = true;
    lastPos = getPos(e);
    ctx.beginPath();
    ctx.moveTo(lastPos.x, lastPos.y);
    e.preventDefault();
  }
  function moveDraw(e) {
    if (!drawing) return;
    var pos = getPos(e);
    ctx.lineTo(pos.x, pos.y);
    ctx.stroke();
    lastPos = pos;
    if (!_sigHasData[id]) {
      _sigHasData[id] = true;
      if (ph) ph.style.opacity = '0';
      if (wrap) wrap.classList.add('has-sig');
      if (st) st.textContent = 'Signature captured';
    }
    e.preventDefault();
  }
  function endDraw(e) { drawing = false; e.preventDefault(); }

  canvas.addEventListener('mousedown',  startDraw);
  canvas.addEventListener('mousemove',  moveDraw);
  canvas.addEventListener('mouseup',    endDraw);
  canvas.addEventListener('mouseleave', endDraw);
  canvas.addEventListener('touchstart', startDraw, {passive:false});
  canvas.addEventListener('touchmove',  moveDraw,  {passive:false});
  canvas.addEventListener('touchend',   endDraw,   {passive:false});
}

function clearSig(id) {
  var canvas = document.getElementById(id+'-sig-canvas');
  if (!canvas) return;
  var dpr = window.devicePixelRatio || 1;
  canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
  _sigHasData[id] = false;
  var ph = document.getElementById(id+'-sig-placeholder');
  var wrap = document.getElementById(id+'-canvas-wrap');
  var st = document.getElementById(id+'-sig-status');
  if (ph) ph.style.opacity = '';
  if (wrap) wrap.classList.remove('has-sig');
  if (st) st.textContent = '';
}

// &#9472;&#9472; Helpers &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
function _genSubId(name,id){
  var raw=(name||'').toUpperCase().replace(/\\s+/g,'')+'\x7c'+FLIGHT_KEY+'\x7c'+id.toUpperCase()+'\x7c'+new Date().toISOString().slice(0,10);
  var h=5381;for(var i=0;i<raw.length;i++){h=((h<<5)+h)^raw.charCodeAt(i);h=h>>>0;}
  var p1=h.toString(16).toUpperCase().padStart(8,'0');
  var p2=(raw.length*31+7).toString(16).toUpperCase().padStart(4,'0');
  var p3=(raw.split('').reduce(function(a,c){return a+c.charCodeAt(0);},0)%65536).toString(16).toUpperCase().padStart(4,'0');
  return p1+'-'+p2+'-'+p3;
}
function _nowLabel(){var d=_simNow(),mo=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'],dd=String(d.getUTCDate()).padStart(2,'0'),hh=String(d.getUTCHours()).padStart(2,'0'),mm=String(d.getUTCMinutes()).padStart(2,'0');return mo[d.getUTCMonth()]+' '+dd+', '+d.getUTCFullYear()+' \u2014 '+hh+':'+mm+'Z';}
function openSign(){document.getElementById('sign-overlay').style.display='block';document.body.style.overflow='hidden';restoreSignedState();setTimeout(function(){_initCanvas('ofp');_initCanvas('ffd');},50);}
window.openSign = openSign;
window.closeSign = closeSign;
function closeSign(){document.getElementById('sign-overlay').style.display='none';document.body.style.overflow='';}
function signTab(id){
  ['ofp','ffd'].forEach(function(t){
    document.getElementById('stab-'+t).classList.toggle('active',t===id);
    document.getElementById('spanel-'+t).classList.toggle('active',t===id);
  });
  // Re-init canvas in case it was hidden on first load
  setTimeout(function(){_initCanvas(id);},30);
}

// &#9472;&#9472; Submit sign &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
function submitSign(id){
  var nameEl=document.getElementById(id+'-name');
  var certEl=document.getElementById(id+'-cert');
  var name=nameEl?nameEl.value.trim():'';
  var cert=certEl?certEl.value.trim():'';
  // Validate name
  if(!name){
    nameEl.classList.add('error');nameEl.focus();
    setTimeout(function(){nameEl.classList.remove('error');},2000);
    return;
  }
  // Validate cert
  if(!cert){
    certEl.classList.add('error');certEl.focus();
    setTimeout(function(){certEl.classList.remove('error');},2000);
    return;
  }
  // Validate signature
  if(!_sigHasData[id]){
    var wrap=document.getElementById(id+'-canvas-wrap');
    if(wrap){wrap.style.borderColor='rgba(220,80,80,0.6)';wrap.style.boxShadow='0 0 0 3px rgba(220,80,80,0.08)';}
    setTimeout(function(){if(wrap){wrap.style.borderColor='';wrap.style.boxShadow='';}},2000);
    return;
  }
  var ts=_nowLabel(),subId=_genSubId(name,id);
  _signed[id]={ts:ts,subId:subId,unix:Date.now(),name:name,cert:cert};
  try{localStorage.setItem(FLIGHT_KEY+'_sign_'+id,JSON.stringify(_signed[id]));}catch(e){}
  _markSigned(id,ts,subId);
  _checkBothSigned();
}

function _markSigned(id,ts,subId){
  // Replace form with confirmation block
  var area=document.getElementById(id+'-signed-area');
  var btn=document.getElementById(id+'-sign-btn');
  var form=btn?btn.parentElement:null;
  if(btn)btn.style.display='none';
  if(area){
    area.style.display='block';
    area.innerHTML=
      "<div class='signed-confirm'>"
      +"<div class='signed-check'>&#10003;</div>"
      +"<div style='color:#4cdf8a;font-size:14px;font-weight:700;letter-spacing:0.5px;'>"
      +(id==='ofp'?'OFP Release Accepted':'Fit for Duty Confirmed')
      +"</div>"
      +"<div class='signed-ts'>"+ts+"</div>"
      +(id==='ofp'?"<div class='signed-id'>SUB ID: "+subId+"</div>":"")
      +"<button data-sid="+id+" onclick='unsign(this.dataset.sid)' style='margin-top:10px;background:transparent;"
      +"color:#e07070;border:1px solid rgba(220,80,80,0.4);border-radius:5px;"
      +"padding:6px 14px;font-size:11px;font-weight:700;letter-spacing:.5px;cursor:pointer;"
      +"text-transform:uppercase;font-family:inherit;'>&#8635; Unsign</button>"
      +"</div>";
  }
  if(id==='ofp' && window._unlockNavlog) _unlockNavlog();
}

function _checkBothSigned(){
  var ffd=_signed['ffd'],ofp=_signed['ofp'];
  if(!ffd||!ofp)return;
  var banners=document.getElementById('sign-banners');
  if(banners)banners.style.display='block';
  var fEl=document.getElementById('banner-ffd-time'),oEl=document.getElementById('banner-ofp-time'),sEl=document.getElementById('banner-sub-id');
  if(fEl)fEl.textContent=ffd.ts;
  if(oEl)oEl.textContent=ofp.ts;
  if(sEl)sEl.textContent=ofp.subId;
  var sb=document.getElementById('sign-btn');
  if(sb){sb.style.color='#4cdf8a';sb.textContent='\\u2713 SIGNED';}
  var remaining=(Math.min(ffd.unix,ofp.unix)+30*60*1000)-Date.now();
  if(remaining>0)setTimeout(hideBanners,remaining);else hideBanners();
}

function _setBannerHeight(){
  document.documentElement.style.setProperty('--banner-h','0px');
}

function hideBanners(){
  var b=document.getElementById('sign-banners');
  if(b)b.style.display='none';
  document.documentElement.style.setProperty('--banner-h','0px');
  try{localStorage.removeItem(FLIGHT_KEY+'_sign_ffd');localStorage.removeItem(FLIGHT_KEY+'_sign_ofp');}catch(e){}
}

function unsign(id){
  delete _signed[id];
  try{localStorage.removeItem(FLIGHT_KEY+'_sign_'+id);}catch(e){}
  var area=document.getElementById(id+'-signed-area');
  var btn=document.getElementById(id+'-sign-btn');
  if(area){area.style.display='none';area.innerHTML='';}
  if(btn)btn.style.display='';
  if(id==='ofp'){
    var bar=document.getElementById('nl-unsigned-bar');
    var banner=document.getElementById('nl-rls-banner');
    if(bar)bar.style.display='flex';
    if(banner)banner.style.display='none';
  }
  var banners=document.getElementById('sign-banners');
  if(banners)banners.style.display='none';
  document.documentElement.style.setProperty('--banner-h','0px');
}

function restoreSignedState(){
  try{
    var fR=localStorage.getItem(FLIGHT_KEY+'_sign_ffd'),oR=localStorage.getItem(FLIGHT_KEY+'_sign_ofp');
    if(fR){_signed['ffd']=JSON.parse(fR);var d=_signed['ffd'];_markSigned('ffd',d.ts,d.subId);}
    if(oR){_signed['ofp']=JSON.parse(oR);var d=_signed['ofp'];_markSigned('ofp',d.ts,d.subId);}
    _checkBothSigned();
  }catch(e){}
}
window.addEventListener('DOMContentLoaded',restoreSignedState);

</script>
"""

    html += """
<script>
// &#9472;&#9472; Simulator timezone offset (hours) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
window._tzOffsetHours = 0;
try { var _saved = localStorage.getItem('av_tz_offset'); if (_saved !== null) window._tzOffsetHours = parseInt(_saved) || 0; } catch(e) {}

function _simNow() {
    // Returns a Date shifted by the simulator timezone offset
    return new Date(Date.now() + window._tzOffsetHours * 3600000);
}

function _tzLabel(offset) {
    if (offset === 0) return 'no offset';
    return (offset > 0 ? '+' : '') + offset + 'hr';
}

function openSettings() {
    document.getElementById('settings-overlay').style.display = 'block';
    document.body.style.overflow = 'hidden';
    _syncSettingsUI();
}


function closeSettings() {
    document.getElementById('settings-overlay').style.display = 'none';
    document.body.style.overflow = '';
}

function _syncSettingsUI() {
    var o = window._tzOffsetHours;
    var disp = document.getElementById('tz-display');
    var slider = document.getElementById('tz-slider');
    if (disp) disp.textContent = _tzLabel(o);
    if (slider) slider.value = o;
}

function adjustTzOffset(delta) {
    var newVal = Math.max(-12, Math.min(14, window._tzOffsetHours + delta));
    window._tzOffsetHours = newVal;
    try { localStorage.setItem('av_tz_offset', newVal); } catch(e) {}
    _syncSettingsUI();
    updateClock();
}

function setTzOffsetFromSlider(val) {
    window._tzOffsetHours = parseInt(val) || 0;
    try { localStorage.setItem('av_tz_offset', window._tzOffsetHours); } catch(e) {}
    _syncSettingsUI();
    updateClock();
}

function resetTzOffset() {
    window._tzOffsetHours = 0;
    try { localStorage.setItem('av_tz_offset', 0); } catch(e) {}
    _syncSettingsUI();
    updateClock();
}

(function() {
    // &#9472;&#9472; Flight state: 'pre' | 'airborne' | 'onblocks' &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    var _flightState = 'pre';
    var _blockOffMs  = 0;
    var _blockOnMs   = 0;

    // Restore from localStorage
    try {
        var _sv = localStorage.getItem('av_flight_state');
        if (_sv) { var _sp = JSON.parse(_sv);
            _flightState = _sp.state || 'pre';
            _blockOffMs  = _sp.offMs  || 0;
            _blockOnMs   = _sp.onMs   || 0;
        }
    } catch(e) {}

    function _saveState() {
        try { localStorage.setItem('av_flight_state', JSON.stringify({
            state: _flightState, offMs: _blockOffMs, onMs: _blockOnMs
        })); } catch(e) {}
    }

    function _setBadge(badge, cls, text) {
        badge.className = 'on-time-badge' + (cls ? ' ' + cls : '');
        badge.textContent = text;
    }

    // Format milliseconds as HHMM (e.g. 75 mins &#8594; "0115")
    function _msToHHMM(ms) {
        var totalMins = Math.floor(Math.abs(ms) / 60000);
        var h = Math.floor(totalMins / 60), m = totalMins % 60;
        return String(h).padStart(2,'0') + String(m).padStart(2,'0');
    }

    // Format elapsed ms as H:MM for in-flight timer
    function _fmtElapsed(ms) {
        var t = Math.floor(ms / 60000);
        var h = Math.floor(t / 60), m = t % 60;
        return h + ':' + String(m).padStart(2,'0');
    }

    function updateStatusBadge() {
        var badge = document.getElementById('status-badge');
        if (!badge) return;
        var outTs = parseInt(badge.dataset.outTs || '0');
        var nowMs = Date.now();

        if (_flightState === 'airborne') {
            // Elapsed since pilot tapped OUT
            _setBadge(badge, 'airborne-badge', '&#9992; ' + _fmtElapsed(nowMs - _blockOffMs));
        } else if (_flightState === 'onblocks') {
            // Total block time
            _setBadge(badge, 'onblocks-badge', '&#11035; ' + _fmtElapsed(_blockOnMs - _blockOffMs));
        } else {
            // Pre-departure: compare sim clock to scheduled OUT
            if (!outTs) { _setBadge(badge, '', 'ON TIME'); return; }
            var diffMs = _simNow().getTime() - outTs * 1000;  // positive = late
            if (diffMs <= 0) {
                _setBadge(badge, '', 'ON TIME -' + _msToHHMM(-diffMs));
            } else {
                _setBadge(badge, 'delayed-badge', 'DELAYED +' + _msToHHMM(diffMs));
            }
        }
    }
    window.updateStatusBadge = updateStatusBadge;

    // Tap once &#8594; OUT, tap again &#8594; ON BLOCKS, tap again &#8594; reset
    window.pillTap = function() {
        if (_flightState === 'pre') {
            _flightState = 'airborne';
            _blockOffMs  = Date.now();
        } else if (_flightState === 'airborne') {
            _flightState = 'onblocks';
            _blockOnMs   = Date.now();
        } else {
            _flightState = 'pre';
            _blockOffMs  = 0;
            _blockOnMs   = 0;
        }
        _saveState();
        updateStatusBadge();
    };

    function updateClock() {
        var now = _simNow();
        var h = String(now.getUTCHours()).padStart(2,'0');
        var m = String(now.getUTCMinutes()).padStart(2,'0');
        var el = document.getElementById('utc-clock');
        if (el) el.textContent = h + ':' + m + ' UTC';
        updateStatusBadge();
    }
    window.updateClock = updateClock;
    updateClock();
    setInterval(updateClock, 1000);

    // Measure real top-bar height and set CSS variable so overlays pad correctly
    function setTopbarHeight() {
        var tb = document.querySelector('.top-bar');
        if (tb) {
            document.documentElement.style.setProperty('--topbar-h', tb.offsetHeight + 'px');
        }
    }
    window.addEventListener('DOMContentLoaded', setTopbarHeight);
    window.addEventListener('load', setTopbarHeight);
    window.addEventListener('resize', setTopbarHeight);
    setTimeout(setTopbarHeight, 100);
    setTimeout(setTopbarHeight, 500);  // extra pass for PWA standalone on iOS
})();
</script>
"""

    # &#9472;&#9472; FUEL & WEIGHTS OVERLAY &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    f  = data['fuel']
    w  = data['weights']

    # &#9472;&#9472; Helper: plain integer display (no zero-padding), dash if empty &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    def _n(val):
        try:
            return str(int(round(float(val or 0))))
        except Exception:
            return '&mdash;'

    def _hhmm(val):
        """Convert HHMM string to HH:MM, return '' if zero/empty."""
        if not val or val in ('0', '0000', '00:00'):
            return ''
        s = str(val).replace(':', '')
        if len(s) >= 4:
            return f"{s[:2]}:{s[2:4]}"
        return val

    _fw_row_counter = [0]  # mutable counter for alternating rows
    def _fw_row_ab(label, fuel_val, time_val='', subdued=False, dash_zero=False):
        """Aviobook-style fuel row. MAX PLND highlight is handled via JS/CSS on tap."""
        try:
            fv = int(round(float(fuel_val or 0)))
            fuel_str = str(fv)
        except Exception:
            fuel_str = '0'
            fv = 0

        tv = _hhmm(time_val)
        if dash_zero:
            time_str = '( - )'
        elif tv:
            time_str = f'({tv})' if label == 'TAXI' else tv
        else:
            if time_val is not None and time_val != '':
                try:
                    time_str = '00:00' if int(float(str(time_val).replace(':','') or 0)) == 0 else ''
                except Exception:
                    time_str = ''
            else:
                time_str = ''

        row_idx = _fw_row_counter[0]
        _fw_row_counter[0] += 1
        if row_idx % 2 == 0:
            row_bg = 'background:rgba(255,255,255,0.05);'
        else:
            row_bg = 'background:rgba(255,255,255,0.02);'

        if subdued:
            lbl_col  = '#7aacca'
            val_col  = '#9ac4d8'
            time_col = '#6a9ab8'
        else:
            lbl_col  = '#c8dff0'
            val_col  = '#ffffff'
            time_col = '#9ec8e0'

        tr_style = f'{row_bg}border-bottom:1px solid rgba(255,255,255,0.06);'
        _sans = "font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;"
        td_lbl  = (f"font-size:13px;font-weight:400;color:{lbl_col};"
                   f"padding:11px 6px 11px 6px;white-space:nowrap;vertical-align:middle;{_sans}")
        td_val  = (f"font-size:13px;font-weight:400;color:{val_col};"
                   f"text-align:right;padding:11px 8px 11px 4px;{_sans}vertical-align:middle;")
        td_rvsd = "padding:4px 4px;vertical-align:middle;width:58px;"
        td_time = (f"font-size:13px;color:{time_col};"
                   f"text-align:right;padding:11px 6px 11px 4px;{_sans}"
                   "white-space:nowrap;vertical-align:middle;min-width:50px;")
        rvsd_inp = ('<input type="text" maxlength="6" '
                    'style="width:100%;box-sizing:border-box;background:transparent;'
                    'border:1px solid transparent;border-radius:3px;'
                    'color:#9ad4f0;font-size:16px;'
                    'font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;'
                    'padding:3px 4px;text-align:right;outline:none;'
                    'transition:border-color 0.15s,background 0.15s;" '
                    "onfocus=\"this.style.background='rgba(255,255,255,0.09)';"
                    "this.style.borderColor='rgba(90,160,210,0.55)'\" "
                    "onblur=\"if(!this.value){this.style.background='transparent';"
                    "this.style.borderColor='transparent'}\">"
                    )
        return (
            f'<tr style="{tr_style}">'
            f'<td style="{td_lbl}">{label}</td>'
            f'<td style="{td_val}">{fuel_str}</td>'
            f'<td style="{td_rvsd}">{rvsd_inp}</td>'
            f'<td style="{td_time}">{time_str}</td>'
            f'</tr>'
        )

    _wt_row_counter = [0]  # mutable counter for alternating rows
    def _wt_row_ab(label, plan_val, max_val=''):
        """Aviobook-style weights row: LABEL | PLND | RVSD | MAX"""
        plan_str = _n(plan_val) if plan_val else ''
        max_str  = _n(max_val)  if max_val  else ''

        row_idx = _wt_row_counter[0]
        _wt_row_counter[0] += 1
        if row_idx % 2 == 0:
            row_bg = 'background:rgba(255,255,255,0.05);'
        else:
            row_bg = 'background:rgba(255,255,255,0.02);'

        tr_style = f'{row_bg}border-bottom:1px solid rgba(255,255,255,0.06);'
        _sans = "font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;"
        td_lbl   = (f"font-size:13px;font-weight:400;color:#c8dff0;"
                    f"padding:11px 6px 11px 6px;white-space:nowrap;vertical-align:middle;{_sans}")
        td_plan  = (f"font-size:13px;font-weight:400;color:#ffffff;"
                    f"text-align:right;padding:11px 8px 11px 4px;{_sans}vertical-align:middle;")
        td_rvsd  = "padding:4px 4px;vertical-align:middle;width:62px;"
        td_max   = (f"font-size:13px;font-weight:700;color:#ffffff;"
                    f"text-align:right;padding:11px 6px 11px 4px;{_sans}vertical-align:middle;")
        rvsd_inp = ('<input type="text" maxlength="7" '
                    'style="width:100%;box-sizing:border-box;background:transparent;'
                    'border:1px solid transparent;border-radius:3px;'
                    'color:#9ad4f0;font-size:16px;'
                    'font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;'
                    'padding:3px 4px;text-align:right;outline:none;'
                    'transition:border-color 0.15s,background 0.15s;" '
                    "onfocus=\"this.style.background='rgba(255,255,255,0.09)';"
                    "this.style.borderColor='rgba(90,160,210,0.55)'\" "
                    "onblur=\"if(!this.value){this.style.background='transparent';"
                    "this.style.borderColor='transparent'}\">")
        return (
            f'<tr style="{tr_style}">'
            f'<td style="{td_lbl}">{label}</td>'
            f'<td style="{td_plan}">{plan_str}</td>'
            f'<td style="{td_rvsd}">{rvsd_inp}</td>'
            f'<td style="{td_max}">{max_str}</td>'
            f'</tr>'
        )

    # &#9472;&#9472; Compute display values &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    try:
        _min_plnd = int(float(f.get('min_takeoff') or 0)) + int(float(f.get('taxi_out') or 0))
    except Exception:
        _min_plnd = 0

    try:
        _taxi_sec = int(float(f.get('t_taxi') or 0)) * 60  # t_taxi is already HHMM
    except Exception:
        _taxi_sec = 0

    # Plan arr fuel time: t_reserve + t_alternate
    try:
        def _hhmm_to_sec(h):
            if not h or h in ('0', '0000', '00:00'): return 0
            s = str(h).replace(':', '')
            return int(s[:2]) * 3600 + int(s[2:4]) * 60
        _paf_sec = _hhmm_to_sec(f.get('t_reserve','')) + _hhmm_to_sec(f.get('t_alternate',''))
        _paf_t = f"{_paf_sec//3600:02d}{(_paf_sec%3600)//60:02d}" if _paf_sec else ''
    except Exception:
        _paf_t = ''

    flt_label   = g.get('icao_airline','') + g.get('flight_number','')
    orig_icao   = a['origin']['icao']
    dest_icao   = a['destination']['icao']
    orig_iata   = a['origin'].get('iata', orig_icao)
    dest_iata   = a['destination'].get('iata', dest_icao)
    orig_elev   = a['origin'].get('elevation', '')
    dest_elev   = a['destination'].get('elevation', '')

    # Format elevations as (NNNN')
    def _elev(e):
        try: return f"({int(float(e or 0))}')"
        except Exception: return ''

    # STE/ETE for preflight ref: HH:MM/HH:MM
    _ste = _hhmm(g.get('ste','')) or _hhmm(t.get('sched_time_enroute',''))
    _ete = _hhmm(g.get('ete','')) or _hhmm(t.get('est_time_enroute',''))
    _ste_ete = f"{_ste}/{_ete}" if (_ste or _ete) else '--/--'

    # Cruise altitude FL
    try:
        _init_alt = int(g.get('initial_altitude', 0) or 0)
        _crz_alt = f"FL{_init_alt // 100}" if _init_alt > 18000 else f"{_init_alt} ft"
    except Exception:
        _crz_alt = ''

    # Cost index
    try:
        _ci = int(float(g.get('cost_index', '0') or 0))
    except Exception:
        _ci = 0

    # ATOG (ramp weight / 1000)
    try:
        _atog = float(w.get('ow') or w.get('takeoff') or 0) / 1000
        _atog_str = f"{_atog:.1f}"
    except Exception:
        _atog_str = '---'

    # TOW in thousands
    try:
        _tow_k = float(w.get('takeoff') or 0) / 1000
        _tow_str = f"{_tow_k:.1f}"
    except Exception:
        _tow_str = '---'

    # Wind direction/speed for preflight reference
    _wind_dir = g.get('avg_wind_dir', '---')
    _wind_spd = g.get('avg_wind_spd', '---')
    try:
        _wind_spd_i = int(float(_wind_spd or 0))
        _wind_spd = f"{_wind_spd_i:03d}"
    except Exception:
        pass
    try:
        _wind_dir_i = int(float(_wind_dir or 0))
        _wind_dir = f"{_wind_dir_i:03d}"
    except Exception:
        pass
    _wind_str = f"{_wind_dir}/{_wind_spd}"

    # Temp dev
    _temp_dev = g.get('avg_temp_dev', '+00')
    try:
        _td = int(float(_temp_dev or 0))
        _temp_dev = f"{'+' if _td >= 0 else ''}{_td:02d}"
    except Exception:
        pass

    # Plan number / release
    _plan_no = g.get('plan_number', '') or data['ofp'].get('time', '') or ''

    # Route for preflight ref &mdash; use atc/route_ifps, fall back to flightplan_text
    _route_str = r.get('route_ifps', '') or r.get('route', '') or ''
    # Trim to just the route portion (between orig and dest if possible)
    if _route_str and orig_icao and dest_icao:
        # Try to strip leading/trailing icao
        _r = _route_str.strip()
        if _r.startswith(orig_icao):
            _r = _r[len(orig_icao):].strip()
        if _r.endswith(dest_icao):
            _r = _r[:-len(dest_icao)].strip()
        _route_str_clean = f"{orig_icao} {_r} {dest_icao}".strip()
    else:
        _route_str_clean = _route_str or f"{orig_icao} DCT {dest_icao}"

    # MEL/CDL
    _mel = g.get('mel_cdl', '') or ''
    _remarks = g.get('remarks', '') or g.get('dx_rmk', '') or ''

    # &#9472;&#9472; Alternates: separate by type &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    # T/O ALTN = type 'TKOF', ALTN 1/2 = type 'DEST' in order
    alts_all  = data.get('alternate', [])
    _tkof_alt = next((x for x in alts_all if x.get('type') == 'TKOF'), None)
    _dest_alts = [x for x in alts_all if x.get('type') == 'DEST']

    _toa_icao = _tkof_alt['icao'] if _tkof_alt else ''
    _toa_fuel = _n(_tkof_alt.get('burn', '')) if _tkof_alt else ''

    _altn1_icao = _dest_alts[0]['icao'] if len(_dest_alts) > 0 else ''
    _altn1_fuel = _n(_dest_alts[0].get('burn', '')) if len(_dest_alts) > 0 else ''
    _altn2_icao = _dest_alts[1]['icao'] if len(_dest_alts) > 1 else ''
    _altn2_fuel = _n(_dest_alts[1].get('burn', '')) if len(_dest_alts) > 1 else ''

    # sched_off/in in HHMMZ UTC, and local HHMML
    _dep_z = sched_off_utc.replace(':', '') + 'Z' if sched_off_utc and sched_off_utc != '--:--' else ''
    _arr_z = sched_in_utc.replace(':', '')  + 'Z' if sched_in_utc  and sched_in_utc  != '--:--' else ''
    _dep_loc_hhmm = sched_off_loc.replace(':', '') if sched_off_loc and sched_off_loc != '--:--' else ''
    _arr_loc_hhmm = sched_in_loc.replace(':', '')  if sched_in_loc  and sched_in_loc  != '--:--' else ''

    # Plan arr fuel display
    _paf_disp   = _n(f.get('plan_landing', ''))
    _paf_t_disp = _hhmm(_paf_t)

    # Min planned = min_takeoff + taxi  (already computed above as _min_plnd)
    _min_plnd_disp = str(_min_plnd) if _min_plnd else ''

    # LIMIT type: derive from weights &mdash; if TOW limited = TAKEOFF, else LANDING
    try:
        _tow_v   = float(w.get('takeoff')   or 0)
        _mtow_v  = float(w.get('max_tow_struct') or w.get('max_tow') or 0)
        _lgw_v   = float(w.get('landing')   or 0)
        _mlgw_v  = float(w.get('max_ldw')   or 0)
        if _mtow_v and abs(_tow_v - _mtow_v) < abs(_lgw_v - _mlgw_v):
            _limit = 'TAKEOFF'
        else:
            _limit = 'LANDING'
    except Exception:
        _limit = 'LANDING'

    # TEMP/DEV: format as -02/+05 style from avg_temp_dev
    _temp_raw = g.get('avg_temp_dev', '0') or '0'
    try:
        _td_val = int(float(_temp_raw))
        _temp_dev_str = f"{'+' if _td_val >= 0 else ''}{_td_val:02d}"
    except Exception:
        _temp_dev_str = _temp_raw

    # &#9472;&#9472; HTML: FUEL & WEIGHTS PANEL (OFP sub-tab) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += ("<div id='tab-fw' style='display:none;position:fixed;top:0;left:0;right:0;bottom:0;"
             "z-index:600;"
             "background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);"
             "overflow-y:auto;-webkit-overflow-scrolling:touch;"
             "padding-top:var(--topbar-h,88px);padding-bottom:80px;'>")

    # CSS for tap-to-highlight on MAX PLND row and RVSD input focus
    html += ("<style>"
             "#tab-fw tr.fw-row-active{border:1.5px solid rgba(74,168,218,0.8)!important;"
             "background:rgba(20,60,95,0.6)!important;}"
             "#tab-fw tr.fw-row-active td{color:#ffffff!important;}"
             "#tab-fw input:focus{border-color:rgba(90,160,210,0.7)!important;"
             "background:rgba(255,255,255,0.09)!important;box-shadow:0 0 0 2px rgba(74,168,218,0.18);}"
             "</style>")

    html += "<div class='overlay-inner'>"

    # &#9472;&#9472; Header row: ALL WEIGHTS IN LB + VIEW ALTERNATES link &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += ("<div style='display:flex;align-items:baseline;justify-content:space-between;"
             "padding:8px 16px 4px 16px;'>")
    html += ("<span style='font-size:11px;font-weight:500;color:#9ec8e0;"
             "letter-spacing:0.4px;"
             "font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;'"
             ">ALL WEIGHTS IN LB</span>")
    html += ("<button "
             "onclick=\"switchSection('briefing');setTimeout(function(){"
             "var el=document.getElementById('sec-alternate');"
             "if(el){el.scrollIntoView({behavior:'smooth',block:'start'});"
             "var body=document.getElementById('sec-alternate-body');"
             "if(body&&body.classList.contains('collapsed')){"
             "body.classList.remove('collapsed');"
             "var hdr=document.getElementById('sec-alternate');"
             "if(hdr)hdr.classList.remove('collapsed');}}"
             "},350)\" "
             "style='background:none;border:none;padding:0;"
             "font-size:11px;font-weight:500;"
             "font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;"
             "color:#4da8da;cursor:pointer;text-decoration:underline;"
             "text-underline-offset:2px;letter-spacing:0.2px;'>"
             "VIEW ALTERNATES</button>")
    html += "</div>"

    # &#9472;&#9472; Fuel ladder (MASTERLOG-style) + Weights side-by-side &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _ch = ("font-size:10px;font-weight:600;color:#9ec8e0;letter-spacing:0.5px;"
           "text-align:right;padding:4px 0 6px 4px;border-bottom:1px solid rgba(200,230,255,0.2);"
           "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;")
    _ch_lbl = ("font-size:10px;font-weight:600;color:#9ec8e0;letter-spacing:0.5px;"
               "padding:4px 0 6px 6px;border-bottom:1px solid rgba(200,230,255,0.2);"
               "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;")

    html += ("<div style='display:grid;grid-template-columns:1fr 1fr;"
             "padding:0 16px 10px 16px;gap:0 20px;align-items:start;'>")

    # &#9472;&#9472; FUEL LADDER (LEFT) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    # Helpers for ladder rows
    _sans = "font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;"
    _mono = "font-family:'Courier New',Courier,monospace;"

    # &#9472;&#9472; Fuel ladder row helpers &mdash; matches weights table exactly &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _ldr_row_counter = [0]
    _wt_sans = "font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;"

    def _ldr_sep():
        return ("<tr><td colspan='3' style='padding:0;line-height:0;'>"
                "<div style='border-top:1px solid rgba(255,255,255,0.06);'></div>"
                "</td></tr>")

    def _ldr_row(label, fuel_val, time_val='', bold=False, accent=False,
                 subdued=False, is_total=False, dest_label=''):
        try:
            fv = int(round(float(fuel_val or 0)))
            fuel_str = str(fv)
        except Exception:
            fuel_str = ''

        time_str = _hhmm(time_val) or ''
        lbl_display = f"{label}{'  ' + dest_label if dest_label else ''}"

        row_idx = _ldr_row_counter[0]
        _ldr_row_counter[0] += 1
        row_bg = 'background:rgba(255,255,255,0.05);' if row_idx % 2 == 0 else 'background:rgba(255,255,255,0.02);'

        tr_style = f'{row_bg}border-bottom:1px solid rgba(255,255,255,0.06);'
        td_lbl  = (f"font-size:13px;font-weight:400;color:#c8dff0;text-align:left;"
                   f"padding:11px 6px;white-space:nowrap;vertical-align:middle;{_wt_sans}")
        td_val  = (f"font-size:13px;font-weight:400;color:#ffffff;"
                   f"text-align:left;padding:11px 4px 11px 8px;{_wt_sans}vertical-align:middle;")
        td_time = (f"font-size:13px;font-weight:400;color:#9abcd0;"
                   f"text-align:right;padding:11px 6px 11px 4px;{_wt_sans}"
                   f"white-space:nowrap;vertical-align:middle;")

        return (f'<tr style="{tr_style}">'
                f'<td style="{td_lbl}">{lbl_display}</td>'
                f'<td style="{td_val}">{fuel_str}</td>'
                f'<td style="{td_time}">{time_str}</td>'
                f'</tr>')



    html += "<div>"
    html += ("<div style='font-size:22px;font-weight:300;color:#ffffff;"
             f"{_sans}padding:10px 0 6px 0;'>Fuel</div>")

    _ch_lft = (_ch.replace("text-align:right;","text-align:left;")
               .replace("padding:4px 0 6px 4px;","padding:4px 4px 6px 8px;"))
    html += "<table style='width:100%;border-collapse:collapse;'>"
    html += "<colgroup><col style='width:65%'><col style='width:20%'><col style='width:15%'></colgroup>"
    html += "<thead><tr>"
    html += f"<th style='{_ch_lbl}'></th>"
    html += f"<th style='{_ch_lft}'>LB</th>"
    html += f"<th style='{_ch}'>TIME</th>"
    html += "</tr></thead>"
    html += "<tbody>"

    # &#9472;&#9472; PLAN ARR FUEL (top of ladder) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += _ldr_row('PLAN ARR FUEL', f.get('plan_landing',''), _paf_t, accent=True)

    # &#9472;&#9472; ENRT BRN &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += _ldr_row(f"ENRT BRN  {dest_iata}", f.get('enroute_burn',''), f.get('t_enroute',''))

    # &#9472;&#9472; Middle rungs: exactly MASTERLOG order &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    # Pre-process fuel_extra buckets into labelled dict (same mapping as MASTERLOG)
    _fd = {'DISP ADD': 0, 'DISP EXTRA': 0, 'MEL': 0, 'HOLD': 0, 'TANKERING': 0}
    _ft = {}  # time per label
    _acf_key = None
    _unknown_buckets = {}

    for _b in f.get('fuel_extra', []):
        _blbl = (_b.get('label') or '').strip().upper()
        try:
            _bv = int(float(_b.get('fuel', 0) or 0))
        except Exception:
            _bv = 0
        if _bv <= 0:
            continue
        try:
            _bt = sec_to_hhmm(int(_b.get('time', 0) or 0))
        except Exception:
            _bt = ''

        if _blbl in ('EXTRA',):
            _fd['DISP ADD'] += _bv
            if _bt: _ft['DISP ADD'] = _bt
        elif _blbl in ('ATC', 'WXX'):
            _fd['HOLD'] += _bv
            if _bt: _ft['HOLD'] = _bt
        elif _blbl in ('FOD ADD', 'FOB ADD'):
            _fd['DISP EXTRA'] += _bv
            if _bt: _ft['DISP EXTRA'] = _bt
        elif _blbl == 'TANKERING':
            _fd['TANKERING'] += _bv
            if _bt: _ft['TANKERING'] = _bt
        elif _blbl == 'MEL':
            _fd['MEL'] += _bv
            if _bt: _ft['MEL'] = _bt
        elif _blbl in ('ACF90', 'ACF 90', 'ACF_90'):
            _acf_key = 'ACF90'; _fd['ACF90'] = _bv
            if _bt: _ft['ACF90'] = _bt
        elif _blbl in ('ACF99', 'ACF 99', 'ACF_99', 'PBCF'):
            _acf_key = 'ACF99'; _fd['ACF99'] = _bv
            if _bt: _ft['ACF99'] = _bt
        else:
            _unknown_buckets[_blbl] = _bv
            if _bt: _ft[_blbl] = _bt

    # ACF/PBCF (above RSV)
    if _acf_key and _fd.get(_acf_key, 0) > 0:
        html += _ldr_row(_acf_key, str(_fd[_acf_key]), _ft.get(_acf_key, ''))

    # RSV
    html += _ldr_row('RSV', f.get('reserve',''), f.get('t_reserve',''))

    # E/RSV (contingency, if >0)
    try:
        _cont_v = int(float(f.get('contingency','0') or 0))
    except Exception:
        _cont_v = 0
    if _cont_v > 0:
        html += _ldr_row('E/RSV', f.get('contingency',''), f.get('t_contingency',''))

    # DISP ADD (if >0)
    if _fd.get('DISP ADD', 0) > 0:
        html += _ldr_row('DISP ADD', str(_fd['DISP ADD']), _ft.get('DISP ADD', ''))

    # ALTN (dest alternates)
    for _alt in _dest_alts[:2]:
        _alt_icao = _alt.get('icao','')
        _alt_burn = _alt.get('burn','0')
        _alt_ete  = _alt.get('ete','')
        try:
            _alt_ete_hhmm = sec_to_hhmm(int(_alt_ete)) if str(_alt_ete).isdigit() else _alt_ete
        except Exception:
            _alt_ete_hhmm = ''
        html += _ldr_row(f"ALTN  {_alt_icao}" if _alt_icao else 'ALTN', _alt_burn, _alt_ete_hhmm)

    # ETOPS ADD (if >0 and not already handled as ACF)
    try:
        _etops_v = int(float(f.get('etops','0') or 0))
    except Exception:
        _etops_v = 0
    if _etops_v > 0 and _acf_key is None:
        html += _ldr_row('ETOPS ADD', str(_etops_v), f.get('t_etops',''))

    # HOLD (if >0)
    if _fd.get('HOLD', 0) > 0:
        html += _ldr_row('HOLD', str(_fd['HOLD']), _ft.get('HOLD', ''))

    # DISP EXTRA, MEL, TANKERING (if >0)
    for _lbl in ('DISP EXTRA', 'MEL', 'TANKERING'):
        if _fd.get(_lbl, 0) > 0:
            html += _ldr_row(_lbl, str(_fd[_lbl]), _ft.get(_lbl, ''))

    # Unknown buckets
    for _lbl, _bv in _unknown_buckets.items():
        html += _ldr_row(_lbl, str(_bv), _ft.get(_lbl, ''))


    # &#9472;&#9472; T/O FUEL &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += _ldr_row('T/O FUEL', f.get('plan_takeoff',''), '')
    _mit_idx = _ldr_row_counter[0]; _ldr_row_counter[0] += 1
    _mit_bg = 'background:rgba(255,255,255,0.05);' if _mit_idx % 2 == 0 else 'background:rgba(255,255,255,0.02);'
    html += (f'<tr style="{_mit_bg}border-bottom:1px solid rgba(255,255,255,0.06);">'
             f'<td style="font-size:13px;font-weight:400;color:#c8dff0;text-align:left;padding:11px 6px;{_wt_sans}">MIN T/O</td>'
             f'<td style="font-size:13px;font-weight:400;color:#ffffff;text-align:left;padding:11px 4px 11px 8px;{_wt_sans}">{_n(f.get("min_takeoff",""))}</td>'
             f'<td></td>'
             '</tr>')

    # &#9472;&#9472; TAXI &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += _ldr_row(f"TAXI  {orig_iata}", f.get('taxi_out',''), f.get('t_taxi',''))


    # &#9472;&#9472; TOTAL / RLS FUEL &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += _ldr_row(f"RLS FUEL  {orig_iata}", f.get('plan_ramp',''), r.get('endurance',''))

    html += "</tbody></table>"
    html += "</div>"

    # &#9472;&#9472; WEIGHTS table (RIGHT) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div>"
    html += ("<div style='font-size:22px;font-weight:300;color:#ffffff;"
             "font-family:-apple-system,BlinkMacSystemFont,\"Helvetica Neue\",Arial,sans-serif;"
             "padding:10px 0 6px 0;'>Weights</div>")
    html += "<table style='width:100%;border-collapse:collapse;'>"
    html += "<thead><tr>"
    html += f"<th style='{_ch_lbl}'></th>"
    html += f"<th style='{_ch}'>PLND (LB)</th>"
    html += f"<th style='{_ch}'>RVSD (LB)</th>"
    html += f"<th style='{_ch}'>MAX (LB)</th>"
    html += "</tr></thead>"
    html += "<tbody>"
    html += _wt_row_ab('OEW',      w.get('oew',''),        '')
    html += _wt_row_ab('PYLD',     w.get('payload',''),    '')
    html += _wt_row_ab('ZFW',      w.get('zero_fuel',''),  w.get('max_zfw',''))
    html += _wt_row_ab('MAX PLND', f.get('plan_ramp',''),  '')
    html += _wt_row_ab('TOW',      w.get('takeoff',''),    w.get('max_tow_struct','') or w.get('max_tow',''))
    html += _wt_row_ab('LGW',      w.get('landing',''),    w.get('max_ldw',''))
    html += '</tbody></table></div>'

    html += '</div>'  # end grid

    # &#9472;&#9472; Preflight Reference block &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += ("<style>"
             "#preflight-ref, #preflight-ref *{"
             "font-family:'Courier New',Courier,monospace !important;}"
             "#preflight-ref .pf-title{"
             "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif !important;"
             "}"
             "#preflight-ref table{width:100%;border-collapse:collapse;table-layout:fixed;}"
             "#preflight-ref td{vertical-align:top;white-space:pre-wrap;}"
             "@media(max-width:540px){"
             "#preflight-ref table,#preflight-ref tr{"
             "display:block;}"
             "#preflight-ref td{"
             "display:block;width:100% !important;"
             "padding:0 0 12px 0 !important;"
             "border-left:none !important;"
             "}"
             "}"
             "</style>")
    # Outer box
    html += ("<div id='preflight-ref' style='"
             "margin:4px 16px 24px 16px;"
             "background:linear-gradient(135deg,#1a4a61 0%,#21546d 100%);"
             "border:1px solid rgba(150,210,245,0.20);"
             "border-radius:6px;"
             "padding:14px 14px 16px 14px;"
             "font-family:'Courier New',Courier,monospace !important;'>")

    # Title
    html += ("<div class='pf-title' style='"
             "font-size:15px;font-weight:400;color:#ffffff;"
             "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif !important;"
             "margin-bottom:10px;'>Preflight Reference</div>")

    _c  = "font-family:'Courier New',Courier,monospace !important;"
    _fs = "font-size:11.5px;color:#ffffff;line-height:1.5;"
    # Equal symmetric padding on all three columns &mdash; no borders
    _td_l = f"width:33.33%;padding:0 16px 14px 0;vertical-align:top;white-space:pre-wrap;{_c}{_fs}"
    _td_c = f"width:33.33%;padding:0 16px 14px 16px;vertical-align:top;white-space:pre-wrap;{_c}{_fs}"
    _td_r = f"width:33.33%;padding:0 0 14px 16px;vertical-align:top;white-space:pre-wrap;{_c}{_fs}"

    html += f"<table style='width:100%;border-collapse:collapse;table-layout:fixed;'>"

    # &#9472;&#9472; Row 1: IFR OFP &ndash; 1 (left)  |  blank  |  PLAN# XXXXXX (right) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += (f"<tr>"
             f"<td style='{_td_l}' colspan='2'>IFR OFP &ndash; 1</td>"
             f"<td style='{_td_r}'>PLAN# {_plan_no}</td>"
             f"</tr>")

    # &#9472;&#9472; Row 2: DEPART / STE&middot;ETE&middot;FL&middot;CI / ARRIVE &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _dep_block = (f"DEPART:   {_dep_z}\n"
                  f"{orig_icao}/{orig_iata}  {_dep_loc_hhmm}L\n"
                  f"{_elev(orig_elev)}")
    _ctr_block = (f"STE/ETE: {_ste_ete}\n"
                  f"      {_crz_alt}\n"
                  f"COST INDEX: {_ci}")
    _arr_block = (f"ARRIVE:   {_arr_z}\n"
                  f"{dest_icao}/{dest_iata}  {_arr_loc_hhmm}L\n"
                  f"{_elev(dest_elev)}")
    html += (f"<tr>"
             f"<td style='{_td_l}'>{_dep_block}</td>"
             f"<td style='{_td_c}'>{_ctr_block}</td>"
             f"<td style='{_td_r}'>{_arr_block}</td>"
             f"</tr>")

    # &#9472;&#9472; Row 3: ATOG/TOW/LIMIT | MIN PLANNED/TAKEOFF/ARR FUEL | WIND/TEMP &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _left3  = (f"ATOG: {_atog_str}\n"
               f" TOW: {_tow_str}\n"
               f"LIMIT: {_limit}")
    _mid3   = (f"MIN PLANNED  {_min_plnd_disp}\n"
               f"MIN TAKEOFF  {_n(f.get('min_takeoff',''))}\n"
               f"PLAN ARR     {_paf_disp}  {_paf_t_disp}")
    _right3 = (f"WIND: {_wind_str}\n"
               f"TEMP/DEV: {_temp_dev_str}")
    html += (f"<tr>"
             f"<td style='{_td_l}'>{_left3}</td>"
             f"<td style='{_td_c}'>{_mid3}</td>"
             f"<td style='{_td_r}'>{_right3}</td>"
             f"</tr>")

    # &#9472;&#9472; Row 4: T/O ALTN | ALTN 1 | ALTN 2 &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _toa_block   = f"T/O ALTN: {_toa_icao}\n    FUEL: {_toa_fuel}"
    _altn1_block = f"ALTN 1: {_altn1_icao}\n  FUEL: {_altn1_fuel}"
    _altn2_block = f"ALTN 2: {_altn2_icao}\n  FUEL: {_altn2_fuel}"
    html += (f"<tr>"
             f"<td style='{_td_l}'>{_toa_block}</td>"
             f"<td style='{_td_c}'>{_altn1_block}</td>"
             f"<td style='{_td_r}'>{_altn2_block}</td>"
             f"</tr>")

    html += "</table>"

    # &#9472;&#9472; ROUTE, REMARKS, MEL/CDL &mdash; full-width, each with blank-line gap above &#9472;&#9472;
    _div_style = f"style='{_c}{_fs}margin-top:2px;white-space:pre-wrap;word-break:break-all;'"
    html += (f"<div {_div_style}>"
             f"ROUTE:\n{_route_str_clean}"
             f"</div>")

    html += (f"<div style='{_c}{_fs}margin-top:10px;white-space:pre-wrap;'>"
             f"REMARKS:\n{_remarks if _remarks else ''}"
             f"</div>")

    html += (f"<div style='{_c}{_fs}margin-top:10px;white-space:pre-wrap;'>"
             f"MEL/CDL:\n{_mel if _mel else ''}"
             f"</div>")

    html += "</div>"  # preflight reference
    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-fw

    # &#9472;&#9472; FLIGHTBOX &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _tab_overlay_style = ('display:none;position:fixed;top:0;left:0;right:0;bottom:0;'
                          'z-index:600;'
                          'background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);'
                          'overflow-y:auto;-webkit-overflow-scrolling:touch;'
                          'padding-top:var(--topbar-h,88px);padding-bottom:80px;')
    _fb_orig     = a['origin']['icao']
    _fb_dest     = a['destination']['icao']
    _fb_fltnum   = (g.get('icao_airline','') + g.get('flight_number','')).strip().upper()
    _fb_sb_links = data.get('files', [])


    # SimBrief remote links — passed to JS as seed attachments
    import json as _json

    # Remarks from SimBrief for the "messages" section
    _fb_disp_remarks = data.get('general', {}).get('dx_rmk', '') or ''
    _fb_gen_remarks  = data.get('ofp', {}).get('general_remark', '') or ''

    html += f"<div id='tab-flightbox' style='{_tab_overlay_style}'>"
    html += "<div class='overlay-inner'>"
    html += """
<style>
/* &#9472;&#9472; FlightBox styles &#9472;&#9472; */
#fb-search-bar {
  display:flex; align-items:center; gap:8px;
  margin:10px 14px 0; gap:8px;
}
#fb-search-input {
  flex:1; background:rgba(255,255,255,0.07); border:1px solid rgba(90,160,210,0.25);
  border-radius:8px; color:#d8f0ff; font-size:16px; padding:10px 14px;
  outline:none; font-family:inherit;
}
#fb-search-input::placeholder { color:#4a7080; }
.fb-mark-btn {
  flex-shrink:0; background:rgba(30,80,120,0.5); border:1.5px solid rgba(90,160,210,0.3);
  border-radius:8px; color:#d8f0ff; font-size:11px; font-weight:700; letter-spacing:0.5px;
  padding:10px 12px; cursor:pointer; text-align:center; line-height:1.2;
}
.fb-section-title {
  font-size:20px; font-weight:400; color:#ffffff;
  margin:18px 14px 10px; letter-spacing:0.1px;
}
/* Message rows */
.fb-msg-row {
  display:flex; align-items:center; justify-content:space-between;
  padding:14px 16px; border-top:1px solid rgba(90,160,210,0.15);
  cursor:pointer; gap:10px;
}
.fb-msg-row:last-child { border-bottom:1px solid rgba(90,160,210,0.15); }
.fb-msg-title { color:#d8f0ff; font-size:14px; font-weight:600; flex:1; }
.fb-msg-date  { color:#4a7a96; font-size:12px; white-space:nowrap; }
.fb-msg-chev  { color:#4a7a96; font-size:16px; margin-left:6px; }
.fb-msg-body  {
  display:none; padding:12px 16px 16px; color:#9ec8e0; font-size:13px;
  line-height:1.6; white-space:pre-wrap; border-bottom:1px solid rgba(90,160,210,0.15);
}
/* Attachment list rows */
.fb-att-grid {
  display:flex; flex-direction:column; gap:0;
  margin:0 0 20px;
}
.fb-att-card {
  display:flex; align-items:center; gap:12px;
  padding:10px 14px; cursor:pointer;
  border-bottom:1px solid rgba(90,160,210,0.1);
  transition:background .15s;
}
.fb-att-card:first-child { border-top:1px solid rgba(90,160,210,0.1); }
.fb-att-card:active { background:rgba(90,160,210,0.08); }
/* PDF preview thumbnail */
.fb-att-preview {
  flex-shrink:0; width:40px; height:52px;
  background:#0d2a3a; border:1px solid rgba(90,160,210,0.2);
  border-radius:4px; overflow:hidden; position:relative;
  display:flex; align-items:center; justify-content:center;
}
.fb-att-preview canvas {
  width:100%; height:100%; object-fit:cover; display:block;
}
.fb-att-preview-icon {
  font-size:20px; color:#2a6a9a; line-height:1;
}
/* Text block */
.fb-att-info { flex:1; min-width:0; }
.fb-att-label {
  color:#d8f0ff; font-size:13px; font-weight:600;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.fb-att-filename {
  color:#4a7a96; font-size:11px; margin-top:2px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.fb-att-badge {
  flex-shrink:0; font-size:10px; font-weight:700; letter-spacing:.5px;
  padding:3px 7px; border-radius:4px; text-transform:uppercase;
}
.fb-att-badge-rls { background:rgba(74,180,100,0.2); color:#4ab464; border:1px solid rgba(74,180,100,0.3); }
.fb-att-badge-wb  { background:rgba(90,160,210,0.2); color:#5ab0e0; border:1px solid rgba(90,160,210,0.3); }
.fb-att-badge-ext { background:rgba(180,140,60,0.2); color:#c8a840; border:1px solid rgba(180,140,60,0.3); }
.fb-att-chev { color:#2a6a8b; font-size:18px; flex-shrink:0; }
</style>
"""

    # &#9472;&#9472; Search bar &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += ("<div id='fb-search-bar'>"
             "<input id='fb-search-input' type='search' placeholder='Search…'>"
             "<button class='fb-mark-btn'>MARK ALL<br>AS READ</button>"
             "</div>")

    # &#9472;&#9472; Messages section &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += "<div class='fb-section-title'>Messages</div>"
    html += "<div id='fb-messages'>"

    _fb_msgs = []
    if _fb_disp_remarks:
        _fb_msgs.append(('Dispatcher Remarks', _fb_disp_remarks))
    if _fb_gen_remarks:
        _fb_msgs.append(('General Remarks', _fb_gen_remarks))
    # Always add crew info if available
    _crew = data.get('crew', {})
    _crew_lines = []
    if _crew.get('cpt'):  _crew_lines.append(f"Captain:   {_crew['cpt']}")
    if _crew.get('fo'):   _crew_lines.append(f"First Off: {_crew['fo']}")
    if _crew_lines:
        _fb_msgs.append(('Crew Information', '\n'.join(_crew_lines)))

    if not _fb_msgs:
        html += ("<div style='padding:16px;color:#4a7a96;font-size:13px;'>"
                 "No messages for this flight.</div>")
    else:
        for _msg_title, _msg_body in _fb_msgs:
            _msg_id = 'fbmsg-' + _msg_title.replace(' ','-').lower()
            html += (f"<div class='fb-msg-row' onclick='fbToggleMsg(\"{_msg_id}\")'>"
                     f"<span class='fb-msg-title'>{_msg_title}</span>"
                     f"<span class='fb-msg-chev' id='{_msg_id}-chev'>&#8964;</span>"
                     f"</div>"
                     f"<div class='fb-msg-body' id='{_msg_id}'>{_msg_body}</div>")

    html += "</div>"  # fb-messages

    # ── Attachments section (dynamic — folder picker handled entirely client-side) ──
    # SimBrief remote links are still passed as seed attachments.
    _fb_sb_att_js = _json.dumps([
        {'label': sf['name'], 'name': sf['name'], 'doc_type': '', 'uri': sf['link'], 'ext': 'remote'}
        for sf in _fb_sb_links
    ])

    html += f"""
<div class='fb-section-title' id='fb-att-title'>Attachments</div>

<!-- Folder picker bar -->
<div id='fb-folder-bar' style='margin:0 14px 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;'>
  <button id='fb-pick-btn' onclick='fbPickFolder()'
    style='background:linear-gradient(90deg,#1a5a8a,#1e70a8);border:none;border-radius:8px;
           color:#fff;font-size:12px;font-weight:700;letter-spacing:.5px;padding:10px 16px;
           cursor:pointer;text-transform:uppercase;white-space:nowrap;flex-shrink:0;'>
    &#128193; Choose Folder
  </button>
  <div id='fb-folder-label'
    style='flex:1;font-size:12px;color:#4a8aa8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;'>
    No folder selected
  </div>
  <button id='fb-clear-folder-btn' onclick='fbClearFolder()'
    style='display:none;flex-shrink:0;background:transparent;border:1px solid rgba(90,160,210,0.3);
           border-radius:6px;color:#4a8aa8;font-size:11px;padding:6px 10px;cursor:pointer;'>
    Clear
  </button>
  <!-- webkitdirectory triggers native folder picker popup -->
  <input type='file' id='fb-folder-input' accept='.pdf' multiple
         webkitdirectory mozdirectory directory
         style='display:none' onchange='fbFolderSelected(this)'>
</div>

<!-- Scanning indicator -->
<div id='fb-scan-status' style='display:none;padding:4px 14px 10px;color:#4a9ad4;font-size:12px;'></div>

<!-- Attachment grid (populated by JS) -->
<div id='fb-att-grid-wrap'>
  <div id='fb-att-grid' class='fb-att-grid'></div>
  <div id='fb-att-empty' style='padding:0 14px 20px;color:#4a7a96;font-size:13px;display:none;'>
    No matching PDFs found in this folder.
  </div>
</div>

<script>
(function(){{
  var FLIGHT_ORIG = {_json.dumps(_fb_orig)};
  var FLIGHT_DEST = {_json.dumps(_fb_dest)};
  var FLIGHT_NUM  = {_json.dumps(_fb_fltnum)};
  var SB_ATTS     = {_fb_sb_att_js};

  var _fileMap = {{}};
  var _attList = [];

  var LS_NAME_KEY = 'av_folder_name';

  function _savedFolderName() {{
    try {{ return localStorage.getItem(LS_NAME_KEY) || ''; }} catch(e) {{ return ''; }}
  }}
  function _saveFolderName(n) {{
    try {{ if (n) localStorage.setItem(LS_NAME_KEY, n);
          else   localStorage.removeItem(LS_NAME_KEY); }} catch(e) {{}}
  }}

  window.fbPickFolder = function() {{
    document.getElementById('fb-folder-input').click();
  }};

  window.fbClearFolder = function() {{
    _fileMap = {{}};
    _attList = [];
    _saveFolderName('');
    document.getElementById('fb-folder-label').textContent = 'No folder selected';
    document.getElementById('fb-clear-folder-btn').style.display = 'none';
    document.getElementById('fb-folder-input').value = '';
    _renderAtts(SB_ATTS.length ? SB_ATTS : []);
  }};

  window.fbFolderSelected = function(input) {{
    var files = Array.from(input.files || []);
    if (!files.length) return;

    var folderName = (files[0].webkitRelativePath)
      ? files[0].webkitRelativePath.split('/')[0]
      : files.length + ' file' + (files.length !== 1 ? 's' : '');

    _fileMap = {{}};
    files.forEach(function(f) {{
      if (f.name.toLowerCase().endsWith('.pdf')) _fileMap[f.name] = f;
    }});

    var pdfCount = Object.keys(_fileMap).length;
    document.getElementById('fb-folder-label').textContent =
      folderName + ' \u2014 ' + pdfCount + ' PDF' + (pdfCount !== 1 ? 's' : '');
    document.getElementById('fb-clear-folder-btn').style.display = '';
    _saveFolderName(folderName);

    if (!pdfCount) {{ _renderAtts(SB_ATTS.length ? SB_ATTS : []); return; }}
    _scanAndRender(Object.keys(_fileMap));
  }};

  function _scanAndRender(pdfNames) {{
    var status = document.getElementById('fb-scan-status');
    status.textContent = 'Scanning\u2026';
    status.style.display = 'block';

    fetch('/match-pdfs', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ filenames: pdfNames,
                              orig: FLIGHT_ORIG, dest: FLIGHT_DEST, flight: FLIGHT_NUM }})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(resp) {{
      status.style.display = 'none';
      var matches = (resp.matches || []).filter(function(m) {{ return m.score >= 20; }});
      if (!matches.length) {{ _renderAtts(SB_ATTS.length ? SB_ATTS : []); return; }}

      var toRead  = matches.slice(0, 4);
      var pending = toRead.length;
      var results = new Array(toRead.length);

      toRead.forEach(function(m, i) {{
        var file = _fileMap[m.name];
        if (!file) {{
          results[i] = {{ label: _docLabel(m), name: m.name, doc_type: m.doc_type, uri: '', ext: 'local' }};
          if (--pending === 0) _finaliseAtts(results);
          return;
        }}
        var reader = new FileReader();
        reader.onload = function(e) {{
          results[i] = {{ label: _docLabel(m), name: m.name, doc_type: m.doc_type, uri: e.target.result, ext: 'local' }};
          if (--pending === 0) _finaliseAtts(results);
        }};
        reader.onerror = function() {{
          results[i] = {{ label: m.name, name: m.name, doc_type: m.doc_type, uri: '', ext: 'local' }};
          if (--pending === 0) _finaliseAtts(results);
        }};
        reader.readAsDataURL(file);
      }});
    }})
    .catch(function(err) {{
      document.getElementById('fb-scan-status').textContent = 'Scan error: ' + err;
    }});
  }}

  function _docLabel(m) {{
    if (m.doc_type === 'RLS') return 'Operational Flt Release';
    if (m.doc_type === 'WB')  return 'Takeoff Landing Data';
    return m.name.replace(/\\.pdf$/i, '');
  }}

  function _finaliseAtts(localAtts) {{
    _attList = localAtts.filter(function(a) {{ return a.uri; }}).concat(SB_ATTS);
    _renderAtts(_attList);
  }}

  // ── PDF.js preview helper ────────────────────────────────────────────────
  var _PDFJS_LOADED = false;
  function _loadPdfJs(cb) {{
    if (window.pdfjsLib) {{ cb(); return; }}
    if (_PDFJS_LOADED) {{ setTimeout(function(){{ _loadPdfJs(cb); }}, 100); return; }}
    _PDFJS_LOADED = true;
    var s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
    s.onload = function() {{
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
      cb();
    }};
    document.head.appendChild(s);
  }}

  function _renderPdfThumb(dataUri, canvas) {{
    _loadPdfJs(function() {{
      try {{
        var data = atob(dataUri.split(',')[1]);
        var arr  = new Uint8Array(data.length);
        for (var i = 0; i < data.length; i++) arr[i] = data.charCodeAt(i);
        pdfjsLib.getDocument({{data: arr}}).promise.then(function(pdf) {{
          return pdf.getPage(1);
        }}).then(function(page) {{
          var vp = page.getViewport({{scale: 0.3}});
          canvas.width  = vp.width;
          canvas.height = vp.height;
          page.render({{canvasContext: canvas.getContext('2d'), viewport: vp}}).promise.then(function() {{
            // Hide the fallback icon once canvas is drawn
            var icon = canvas.parentNode.querySelector('.fb-att-preview-icon');
            if (icon) icon.style.display = 'none';
            canvas.style.display = 'block';
          }});
        }}).catch(function() {{}});
      }} catch(e) {{}}
    }});
  }}

  function _renderAtts(atts) {{
    var grid  = document.getElementById('fb-att-grid');
    var empty = document.getElementById('fb-att-empty');
    grid.innerHTML = '';
    _attList = atts;

    if (!atts.length) {{ empty.style.display = ''; return; }}
    empty.style.display = 'none';

    atts.forEach(function(att) {{
      // Badge by doc type
      var badgeCls = 'fb-att-badge-ext', badgeTxt = 'PDF';
      if (att.doc_type === 'RLS' || att.label === 'Operational Flt Release')
        {{ badgeCls = 'fb-att-badge-rls'; badgeTxt = 'RLS'; }}
      else if (att.doc_type === 'WB' || att.label === 'Takeoff Landing Data')
        {{ badgeCls = 'fb-att-badge-wb'; badgeTxt = 'W&B'; }}
      else if (att.ext === 'remote')
        {{ badgeCls = 'fb-att-badge-ext'; badgeTxt = 'LINK'; }}

      // Display name: label for known types, filename stem otherwise
      var displayName = att.label || att.name || '';
      var fileName    = att.name  || (att.uri && att.ext === 'remote' ? att.label : '') || '';

      var card = document.createElement('div');
      card.className = 'fb-att-card';
      card.setAttribute('data-search', (displayName + ' ' + fileName).toLowerCase());

      var previewId = 'fb-prev-' + Math.random().toString(36).slice(2);
      card.innerHTML =
        '<div class="fb-att-preview" id="' + previewId + '">' +
          '<span class="fb-att-preview-icon">&#128196;</span>' +
          '<canvas style="display:none;width:100%;height:100%;object-fit:cover;"></canvas>' +
        '</div>' +
        '<div class="fb-att-info">' +
          '<div class="fb-att-label fb-att-lbl">' + displayName + '</div>' +
          (fileName && fileName !== displayName
            ? '<div class="fb-att-filename">' + fileName + '</div>'
            : '') +
        '</div>' +
        '<span class="fb-att-badge ' + badgeCls + '">' + badgeTxt + '</span>' +
        '<span class="fb-att-chev">&#8250;</span>';

      card.addEventListener('click', function() {{ _openAtt(att); }});
      grid.appendChild(card);

      // Render PDF preview thumbnail if we have a data URI
      if (att.uri && att.uri.indexOf('data:application/pdf') === 0) {{
        var canvas = card.querySelector('canvas');
        _renderPdfThumb(att.uri, canvas);
      }}
    }});

    var si = document.getElementById('fb-search-input');
    if (si && si.value) window.fbApplySearch(si.value);
  }}

  function _openAtt(att) {{
    if (!att.uri) return;
    if (att.uri.indexOf('data:') === 0) {{
      try {{
        var parts  = att.uri.split(',');
        var mime   = parts[0].split(':')[1].split(';')[0];
        var binary = atob(parts[1]);
        var bytes  = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        var blob = new Blob([bytes], {{type: mime}});
        window.open(URL.createObjectURL(blob), '_blank');
      }} catch(e) {{ window.open(att.uri, '_blank'); }}
    }} else {{
      window.open(att.uri, '_blank');
    }}
  }}

  // ── Message toggle ────────────────────────────────────────────────────────
  window.fbToggleMsg = function(id) {{
    var body = document.getElementById(id);
    var chev = document.getElementById(id+'-chev');
    if (!body) return;
    var open = body.style.display === 'block';
    body.style.display = open ? 'none' : 'block';
    if (chev) chev.innerHTML = open ? '&#8964;' : '&#8963;';
  }};

  // ── Search / filter ───────────────────────────────────────────────────────
  window.fbApplySearch = function(q) {{
    q = q.toLowerCase().trim();

    var msgRows = document.querySelectorAll('#fb-messages .fb-msg-row');
    msgRows.forEach(function(row) {{
      var m = row.getAttribute('onclick') && row.getAttribute('onclick').match(/"([^"]+)"/);
      var body = m ? document.getElementById(m[1]) : null;
      var text = (row.textContent + (body ? body.textContent : '')).toLowerCase();
      var show = !q || text.indexOf(q) !== -1;
      row.style.display = show ? '' : 'none';
      if (body && !show) body.style.display = 'none';
    }});

    document.querySelectorAll('#fb-att-grid .fb-att-card').forEach(function(card) {{
      var haystack = (card.getAttribute('data-search') || card.textContent).toLowerCase();
      card.style.display = (!q || haystack.indexOf(q) !== -1) ? '' : 'none';
    }});

    var msgsVis = Array.from(msgRows).some(function(r) {{ return r.style.display !== 'none'; }});
    var attsVis = Array.from(document.querySelectorAll('#fb-att-grid .fb-att-card'))
                    .some(function(c) {{ return c.style.display !== 'none'; }});
    document.querySelectorAll('#tab-flightbox .fb-section-title').forEach(function(t) {{
      t.style.display = (t.id === 'fb-att-title' ? attsVis : msgsVis) ? '' : 'none';
    }});
  }};

  var _fbSearchEl = document.getElementById('fb-search-input');
  if (_fbSearchEl) {{
    _fbSearchEl.addEventListener('input',  function() {{ window.fbApplySearch(this.value); }});
    _fbSearchEl.addEventListener('search', function() {{ window.fbApplySearch(this.value); }});
  }}

  // ── Mark all as read ──────────────────────────────────────────────────────
  var _fbMarkBtn = document.querySelector('#tab-flightbox .fb-mark-btn');
  if (_fbMarkBtn) {{
    _fbMarkBtn.addEventListener('click', function() {{
      var btn = this;
      document.querySelectorAll('.fb-msg-row').forEach(function(r) {{ r.style.opacity = '0.45'; }});
      btn.innerHTML = '&#10003; ALL READ';
      setTimeout(function() {{
        document.querySelectorAll('.fb-msg-row').forEach(function(r) {{ r.style.opacity = ''; }});
        btn.innerHTML = 'MARK ALL<br>AS READ';
      }}, 3000);
    }});
  }}

  // ── Init ─────────────────────────────────────────────────────────────────
  (function init() {{
    var saved = _savedFolderName();
    if (saved) {{
      document.getElementById('fb-folder-label').textContent =
        saved + ' \u2014 tap Choose Folder to re-scan';
      document.getElementById('fb-clear-folder-btn').style.display = '';
    }}
    if (SB_ATTS.length) _renderAtts(SB_ATTS);
  }})();

}})();
</script>
"""
    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-flightbox






    # &#9472;&#9472; JOURNEY LOG PANEL (OFP sub-tab) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += f"<div id='tab-journeylog' style='{_tab_overlay_style}'>"
    html += "<div class='overlay-inner'>"
    html += ("<div style='padding:20px 16px 8px;'>"
             "<div style='color:#7ad8fd;font-size:18px;font-weight:300;margin-bottom:4px;'>Journey Log</div>"
             "<div style='color:#4a7a96;font-size:11px;letter-spacing:0.5px;'>Complete after flight</div>"
             "</div>")
    # Journey log form fields
    for field_label, field_id in [
        ("Block Off (UTC)", "jl-block-off"), ("Takeoff (UTC)", "jl-takeoff"),
        ("Landing (UTC)", "jl-landing"), ("Block On (UTC)", "jl-block-on"),
        ("Flight Time (HHMM)", "jl-flight-time"), ("Block Time (HHMM)", "jl-block-time"),
        ("Fuel On Board (lbs)", "jl-fob"), ("Fuel Used (lbs)", "jl-fuel-used"),
        ("Fuel Remaining (lbs)", "jl-fuel-rem"), ("Passengers", "jl-pax"),
        ("Delays / Remarks", "jl-remarks"),
    ]:
        is_textarea = field_id == "jl-remarks"
        html += (f"<div style='margin:0 16px 12px;'>"
                 f"<div style='color:#6ab4d4;font-size:11px;letter-spacing:0.8px;"
                 f"text-transform:uppercase;margin-bottom:5px;'>{field_label}</div>")
        if is_textarea:
            html += (f"<textarea id='{field_id}' rows='3' "
                     f"style='width:100%;box-sizing:border-box;background:rgba(255,255,255,0.06);"
                     f"border:1px solid rgba(90,160,210,0.3);border-radius:5px;color:#e8f6ff;"
                     f"font-size:16px;padding:10px 12px;resize:vertical;font-family:inherit;"
                     f"outline:none;-webkit-user-select:text;user-select:text;' placeholder=''></textarea>")
        else:
            html += (f"<input type='text' id='{field_id}' "
                     f"style='width:100%;box-sizing:border-box;background:rgba(255,255,255,0.06);"
                     f"border:1px solid rgba(90,160,210,0.3);border-radius:5px;color:#e8f6ff;"
                     f"font-size:16px;font-weight:bold;padding:10px 12px;letter-spacing:1px;"
                     f"text-align:center;outline:none;-webkit-user-select:text;user-select:text;' placeholder=''>")
        html += "</div>"
    html += ("<div style='padding:12px 16px 32px;'>"
             "<button onclick='saveJourneyLog()' "
             "style='width:100%;background:linear-gradient(90deg,#1a6a9a,#1e7db8);color:white;"
             "border:none;padding:14px;border-radius:6px;font-size:15px;font-weight:700;"
             "letter-spacing:1px;cursor:pointer;text-transform:uppercase;'>&#10003; Save Journey Log</button>"
             "</div>")
    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-journeylog

    # &#9472;&#9472; EXTRA INFO PANEL (OFP sub-tab) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += f"<div id='tab-extrainfo' style='{_tab_overlay_style}'>"
    html += "<div class='overlay-inner'>"
    html += ("<div style='padding:20px 16px 8px;'>"
             "<div style='color:#7ad8fd;font-size:18px;font-weight:300;margin-bottom:4px;'>Extra Info</div>"
             "<div style='color:#4a7a96;font-size:11px;letter-spacing:0.5px;'>Plan remarks &amp; MEL/CDL</div>"
             "</div>")
    # MEL/CDL
    if g.get('mel_cdl'):
        html += ("<div style='margin:0 16px 16px;background:linear-gradient(135deg,#1a4a61,#21546d);"
                 "border:1px solid rgba(255,180,60,0.3);border-radius:8px;padding:14px;'>"
                 "<div style='color:#ffc060;font-size:11px;font-weight:700;letter-spacing:1px;"
                 "margin-bottom:8px;'>&#9888; MEL / CDL</div>"
                 f"<div style='color:#e8f6ff;font-size:12px;font-family:monospace;"
                 f"white-space:pre-wrap;line-height:1.6;'>{_html_escape.escape(g['mel_cdl'])}</div>"
                 "</div>")
    # DX Remarks
    if g.get('dx_rmk'):
        html += ("<div style='margin:0 16px 16px;background:linear-gradient(135deg,#1a4a61,#21546d);"
                 "border:1px solid rgba(90,174,239,0.2);border-radius:8px;padding:14px;'>"
                 "<div style='color:#5ab8e0;font-size:11px;font-weight:700;letter-spacing:1px;"
                 "margin-bottom:8px;'>&#9998; DISPATCHER REMARKS</div>"
                 f"<div style='color:#e8f6ff;font-size:12px;font-family:monospace;"
                 f"white-space:pre-wrap;line-height:1.6;'>{_html_escape.escape(g['dx_rmk'])}</div>"
                 "</div>")
    # General Remarks
    if g.get('remarks'):
        html += ("<div style='margin:0 16px 16px;background:linear-gradient(135deg,#1a4a61,#21546d);"
                 "border:1px solid rgba(90,174,239,0.2);border-radius:8px;padding:14px;'>"
                 "<div style='color:#5ab8e0;font-size:11px;font-weight:700;letter-spacing:1px;"
                 "margin-bottom:8px;'>&#128221; GENERAL REMARKS</div>"
                 f"<div style='color:#e8f6ff;font-size:12px;font-family:monospace;"
                 f"white-space:pre-wrap;line-height:1.6;'>{_html_escape.escape(g['remarks'])}</div>"
                 "</div>")
    # Cruise profile / step climb
    html += ("<div style='margin:0 16px 16px;background:linear-gradient(135deg,#1a4a61,#21546d);"
             "border:1px solid rgba(90,174,239,0.2);border-radius:8px;padding:14px;'>"
             "<div style='color:#5ab8e0;font-size:11px;font-weight:700;letter-spacing:1px;"
             "margin-bottom:10px;'>&#9992; CRUISE DATA</div>")
    for lbl, val in [
        ("CRUISE PROFILE", g.get('cruise_profile','')),
        ("INITIAL ALT", g.get('initial_altitude','')),
        ("STEP CLIMB", r.get('stepclimb_string','')),
        ("AVG WIND", f"{g.get('avg_wind_dir','')}/{g.get('avg_wind_spd','')} ({g.get('avg_wind_comp','')} kt)"),
        ("ISA DEV", g.get('avg_temp_dev','')),
        ("TROPOPAUSE", g.get('avg_tropopause','')),
    ]:
        if val and val.strip('/').strip():
            html += (f"<div style='display:flex;justify-content:space-between;padding:5px 0;"
                     f"border-bottom:1px solid rgba(255,255,255,0.06);'>"
                     f"<span style='color:#9ec8e0;font-size:11px;letter-spacing:0.5px;'>{lbl}</span>"
                     f"<span style='color:#e8f6ff;font-size:12px;font-weight:600;'>{val}</span>"
                     f"</div>")
    html += "</div>"
    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-extrainfo

    # &#9472;&#9472; ATC FLIGHT PLAN TAB PANEL (BRIEFING section) &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _atc_style = ('display:none;position:fixed;top:0;left:0;right:0;bottom:0;'
                  'z-index:600;background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);'
                  'overflow-y:auto;-webkit-overflow-scrolling:touch;'
                  'padding-top:var(--topbar-h,88px);padding-bottom:80px;')
    html += f"<div id='tab-atc' style='{_atc_style}'>"
    html += "<div class='overlay-inner'>"

    # Header row with COPY ROUTE button
    _atc_route_text = r.get('route_ifps','') or r.get('route','') or ''
    import json as _json_js
    _atc_route_js = _json_js.dumps(_atc_route_text)  # fully safe: handles quotes, backslashes, etc.
    html += ("<div style='display:flex;align-items:center;justify-content:space-between;"
             "padding:16px 16px 12px;'>"
             "<span style='font-size:22px;font-weight:300;color:#ffffff;'>ATC flight plan</span>"
             "<button onclick=\"navigator.clipboard&&navigator.clipboard.writeText("
             + _atc_route_js + ").then(function(){"
             "var b=document.getElementById('atc-copy-btn');"
             "b.innerHTML='&#10003; COPIED';b.style.background='#1a8a4a';"
             "setTimeout(function(){b.innerHTML='&#128272; COPY ROUTE';"
             "b.style.background='';},2000);})\" "
             "id='atc-copy-btn' "
             "style='display:flex;align-items:center;gap:6px;"
             "background:rgba(30,90,130,0.5);border:1.5px solid rgba(90,174,239,0.4);"
             "border-radius:8px;color:#d8f0ff;font-size:12px;font-weight:700;"
             "letter-spacing:0.5px;padding:8px 14px;cursor:pointer;'>&#128272; COPY ROUTE</button>"
             "</div>")

    # Main data card
    _card = ("background:linear-gradient(135deg,#1a4a61,#1e5570);"
             "border:1px solid rgba(90,174,239,0.15);border-radius:10px;"
             "margin:0 16px 14px;padding:16px;")
    _lbl = "font-size:10px;font-weight:700;color:#6ab4d4;letter-spacing:0.8px;text-transform:uppercase;margin-bottom:4px;"
    _val = "font-size:15px;font-weight:500;color:#e8f6ff;margin-bottom:0;"

    # Row helper
    def _atc_row(cols):
        """cols = list of (label, value) tuples"""
        s = f"<div style='display:grid;grid-template-columns:{'1fr '*len(cols)};gap:12px;margin-bottom:14px;'>"
        for lbl, val in cols:
            s += (f"<div><div style='{_lbl}'>{lbl}</div>"
                  f"<div style='{_val}'>{val or '&mdash;'}</div></div>")
        s += "</div>"
        return s

    _atc_id   = r.get('atc_id','') or (g.get('icao_airline','') + g.get('flight_number',''))
    _dep_z2   = data['times']['sched_off'][0] if data['times']['sched_off'] else ''
    _eet_raw  = r.get('eet_atc', '0000').zfill(4)  # pad to at least 4 chars
    _eet_h    = int(_eet_raw[:2] or 0)
    _eet_m    = int(_eet_raw[2:4] or 0)
    _eet_disp = f"{_eet_h}h {_eet_m:02d}m" if (_eet_h or _eet_m) else '&mdash;'
    _alts     = data.get('alternate', [])
    _alt1     = _alts[0]['icao'] if len(_alts) > 0 else '&mdash;'
    _alt2     = _alts[1]['icao'] if len(_alts) > 1 else '&mdash;'

    html += f"<div style='{_card}'>"
    html += _atc_row([("ATC ID", _atc_id), ("RULES", r.get('flight_rules','I')), ("TYPE OF FLIGHT", r.get('flight_type','S'))])
    html += _atc_row([("AIRCRAFT TYPE", r.get('aircraft_icao','') or ac.get('type','')), ("WAKE", r.get('wake_cat','M')), ("EQUIPMENT", r.get('equipment','')), ("ATC SSR", r.get('transponder',''))])
    html += _atc_row([("DEP", a['origin']['icao']), ("TIME", _dep_z2), ("SPEED", r.get('speed_atc','')), ("LEVEL", r.get('level_atc',''))])

    # Route box
    html += (f"<div style='{_lbl}margin-bottom:6px;'>ROUTE</div>"
             f"<div style='background:rgba(0,0,0,0.2);border-radius:6px;padding:10px 12px;"
             f"font-family:monospace;font-size:13px;color:#d8f0ff;line-height:1.6;"
             f"word-break:break-all;margin-bottom:14px;'>{_atc_route_text or '&mdash;'}</div>")

    html += _atc_row([("DEST", a['destination']['icao']), ("EET", _eet_disp), ("ALT 1", _alt1), ("ALT 2", _alt2)])

    # OTHER INFO
    _other = r.get('other_info','')
    if _other:
        html += (f"<div style='{_lbl}margin-bottom:6px;'>INFO</div>"
                 f"<div style='background:rgba(0,0,0,0.2);border-radius:6px;padding:10px 12px;"
                 f"font-family:monospace;font-size:11px;color:#d8f0ff;line-height:1.7;"
                 f"word-break:break-all;margin-bottom:14px;'>{_other}</div>")

    html += _atc_row([("ENDURANCE", r.get('endurance','') or '&mdash;'), ("POB", r.get('pob','') or g.get('passengers','') or '&mdash;')])
    html += "</div>"  # card

    html += "</div>"  # overlay-inner
    html += "</div>"  # tab-atc


    # &#9472;&#9472; SIGN OVERLAY &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _sign_cpt   = _captain_name
    _sign_dx    = (c.get('dx')  or '').strip().upper()
    _sign_rls   = data['ofp'].get('time', '1')
    _sign_flt   = (g.get('icao_airline','') + g.get('flight_number','')).strip()
    _sign_orig  = a['origin']['icao']
    _sign_dest  = a['destination']['icao']
    _disp_phone = data['ofp'].get('telephone') or os.environ.get('AVIOBOOK_DISPATCH_PHONE', '')

    # Build sign overlay HTML with f-string (safe because all JS braces are doubled)
    _so_html  = "<div id='sign-overlay' style='display:none;position:fixed;top:0;left:0;"
    _so_html += "right:0;bottom:0;z-index:1300;"
    _so_html += "overflow-y:auto;-webkit-overflow-scrolling:touch;"
    _so_html += "padding-top:calc(env(safe-area-inset-top,0px) + var(--topbar-h,88px));"
    _so_html += "background:linear-gradient(160deg,#0d3347 0%,#0e4060 50%,#0c3a55 100%);'>"
    _so_html += ("<style>"
        "#sign-overlay .stab{display:inline-block;padding:10px 18px 9px;"
        "font-size:11px;font-weight:700;letter-spacing:0.8px;white-space:nowrap;"
        "cursor:pointer;border-bottom:2px solid transparent;color:#4a7a96;"
        "text-transform:uppercase;transition:color 0.15s;}"
        "#sign-overlay .stab.active{color:#e8f6ff;border-bottom-color:#4da8da;}"
        "#sign-overlay .stab:hover:not(.active){color:#8ab8d0;}"
        "#sign-overlay .spanel{display:none;}"
        "#sign-overlay .spanel.active{display:block;}"
        ".sig-input{background:rgba(13,40,62,0.8);"
        "border:1px solid rgba(74,168,218,0.25);border-radius:5px;"
        "color:#e8f6ff;font-size:13px;font-family:'Courier New',Courier,monospace;"
        "padding:10px 12px;width:100%;box-sizing:border-box;outline:none;"
        "transition:border-color 0.15s,background 0.15s;letter-spacing:0.5px;}"
        ".sig-input:focus{border-color:rgba(74,168,218,0.6);"
        "background:rgba(13,40,62,1);box-shadow:0 0 0 3px rgba(74,168,218,0.08);}"
        ".sig-input::placeholder{color:rgba(74,130,168,0.5);}"
        ".sig-input.error{border-color:rgba(220,80,80,0.7)!important;"
        "box-shadow:0 0 0 3px rgba(220,80,80,0.08)!important;}"
        ".sig-label{font-size:10px;font-weight:700;color:#4a8aaa;"
        "letter-spacing:1px;margin-bottom:6px;text-transform:uppercase;}"
        ".legal-block{font-family:'Courier New',Courier,monospace;"
        "font-size:11.5px;color:#a8c8e0;line-height:1.8;"
        "background:rgba(5,18,30,0.7);"
        "border:1px solid rgba(74,168,218,0.15);"
        "border-left:3px solid rgba(74,168,218,0.4);"
        "border-radius:0 5px 5px 0;"
        "padding:14px 16px;margin-bottom:18px;}"
        ".legal-block .legal-title{font-size:10px;font-weight:700;"
        "color:#4da8da;letter-spacing:2px;margin-bottom:8px;"
        "text-transform:uppercase;}"
        ".legal-block .legal-parties{margin-top:10px;padding-top:10px;"
        "border-top:1px solid rgba(74,168,218,0.12);}"
        ".legal-block .party-row{display:flex;justify-content:space-between;"
        "align-items:center;margin-bottom:4px;gap:8px;}"
        ".legal-block .party-role{color:#6a9ab8;font-size:10px;letter-spacing:1px;"
        "min-width:36px;}"
        ".legal-block .party-name{color:#c8e4f4;font-size:12px;flex:1;}"
        ".legal-block .party-phone{color:#4a7a96;font-size:10px;}"
        ".sig-canvas-wrap{background:rgba(5,18,30,0.7);"
        "border:1px solid rgba(74,168,218,0.25);border-radius:5px;"
        "overflow:hidden;touch-action:none;position:relative;"
        "transition:border-color 0.15s;}"
        ".sig-canvas-wrap:hover{border-color:rgba(74,168,218,0.4);}"
        ".sig-canvas-wrap.has-sig{border-color:rgba(74,168,218,0.5);}"
        ".sig-canvas-wrap canvas{display:block;cursor:crosshair;width:100%;height:90px;}"
        ".sig-placeholder{position:absolute;top:50%;left:50%;"
        "transform:translate(-50%,-50%);"
        "color:rgba(74,130,168,0.35);font-size:12px;font-style:italic;"
        "pointer-events:none;letter-spacing:0.3px;white-space:nowrap;}"
        ".sig-actions{display:flex;justify-content:space-between;"
        "align-items:center;margin-top:4px;}"
        ".sig-clear{font-size:10px;color:#3a6a86;background:none;border:none;"
        "cursor:pointer;padding:2px 0;letter-spacing:0.5px;text-transform:uppercase;"
        "transition:color 0.15s;}"
        ".sig-clear:hover{color:#7ac4e8;}"
        ".sig-status{font-size:10px;color:rgba(74,168,218,0.5);letter-spacing:0.3px;}"
        ".sign-submit-btn{"
        "background:linear-gradient(135deg,rgba(30,100,60,0.4),rgba(20,80,50,0.3));"
        "border:1.5px solid rgba(76,223,138,0.45);border-radius:6px;"
        "color:#4cdf8a;font-size:12px;font-weight:700;"
        "padding:13px 24px;cursor:pointer;letter-spacing:1px;text-transform:uppercase;"
        "width:100%;margin-top:18px;transition:all 0.2s;"
        "box-shadow:0 2px 12px rgba(76,223,138,0.05);}"
        ".sign-submit-btn:hover:not(:disabled){"
        "background:linear-gradient(135deg,rgba(40,130,70,0.5),rgba(30,110,60,0.4));"
        "box-shadow:0 4px 20px rgba(76,223,138,0.15);border-color:rgba(76,223,138,0.65);}"
        ".sign-submit-btn:disabled{opacity:0.25;cursor:default;}"
        ".signed-confirm{text-align:center;padding:24px 16px;"
        "animation:signFadeIn 0.4s ease;}"
        "@keyframes signFadeIn{from{opacity:0;transform:translateY(8px)}"
        "to{opacity:1;transform:translateY(0)}}"
        ".signed-check{display:inline-flex;align-items:center;justify-content:center;"
        "width:52px;height:52px;border-radius:50%;"
        "background:rgba(76,223,138,0.1);border:2px solid rgba(76,223,138,0.4);"
        "font-size:24px;margin-bottom:12px;}"
        ".signed-ts{font-size:11px;color:#4da8da;font-family:monospace;"
        "letter-spacing:0.5px;margin-top:6px;}"
        ".signed-id{font-size:9px;color:#2a5a76;font-family:monospace;"
        "letter-spacing:0.3px;margin-top:4px;}"
        ".ofp-rule{border:none;border-top:1px solid rgba(74,168,218,0.1);margin:10px 0;}"
        "</style>")
    _so_html += ("<div id='sign-tabbar' style='background:linear-gradient(90deg,#0e3a52 0%,#1a4a61 100%);"
        "border-bottom:1px solid #2a6a8a;"
        "padding:0 16px;position:sticky;top:0;z-index:10;"
        "display:flex;align-items:center;touch-action:manipulation;'>"
        "<div class='stab active' id='stab-ofp' onclick='signTab(\"ofp\")'>&#9998; Accept OFP Release</div>"
        "<div class='stab' id='stab-ffd' onclick='signTab(\"ffd\")'>&#10003; Fitness for Duty</div>"
        "<button onclick='closeSign()' style='margin-left:auto;background:rgba(255,255,255,0.06);"
        "border:1px solid rgba(255,255,255,0.12);border-radius:6px;color:#8ab8d0;"
        "font-size:14px;line-height:1;padding:5px 12px;cursor:pointer;'>&#x2715;</button>"
        "</div>")

    _so_html += "<div class='overlay-inner'>"

    # CSS for realistic sign page

    # Header + tabs (sticky) &mdash; no duplicate flight info, real top-bar is always visible above
    # &#9472;&#9472; OFP panel &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _so_html += "<div class='spanel active' id='spanel-ofp' style='padding:18px 16px 32px 16px;'>"

    # Legal document block
    _so_html += ("<div class='legal-block'>"
        "<div class='legal-title'>Operational Flight Plan &mdash; Release Authorization</div>"
        "BY SIGNATURE THE AIRCRAFT DISPATCHER AND THE CAPTAIN BOTH BELIEVE THAT THE FLIGHT CAN BE MADE WITH SAFETY."
        "<div class='legal-parties'>"
        f"<div class='party-row'>"
        f"<span class='party-role'>DSP</span>"
        f"<span class='party-name'>{_sign_dx or 'DISPATCHER'}</span>"
        f"<span class='party-phone'>{_disp_phone}</span>"
        f"</div>"
        f"<div class='party-row'>"
        f"<span class='party-role'>CAP</span>"
        f"<span class='party-name'>{_sign_cpt or 'CAPTAIN'}</span>"
        f"<span class='party-phone'>OFP RLS {_sign_rls}</span>"
        f"</div>"
        "</div>"
        "</div>")

    # Form fields + signature canvas
    _so_html += ("<div style='display:grid;grid-template-columns:1fr 1fr;"
        "gap:12px;margin-bottom:16px;'>"
        "<div><div class='sig-label'>Captain Name</div>"
        f"<input id='ofp-name' class='sig-input' type='text' autocomplete='name' "
        f"autocapitalize='characters' spellcheck='false' "
        f"placeholder='{_sign_cpt or 'LAST FIRST M'}' value=''></div>"
        "<div><div class='sig-label'>EMP #</div>"
        "<input id='ofp-cert' class='sig-input' type='text' inputmode='text' "
        "autocomplete='off' spellcheck='false' maxlength='12' "
        "placeholder='e.g. 4012345'></div></div>")

    # Signature canvas
    _so_html += ("<div class='sig-label'>Signature</div>"
        "<div class='sig-canvas-wrap' id='ofp-canvas-wrap'>"
        "<canvas id='ofp-sig-canvas' height='90'></canvas>"
        "<div class='sig-placeholder' id='ofp-sig-placeholder'>Sign here with finger or mouse</div>"
        "</div>"
        "<div class='sig-actions'>"
        "<span class='sig-status' id='ofp-sig-status'></span>"
        "<button class='sig-clear' onclick='clearSig(\"ofp\")'>&#x2715; Clear</button>"
        "</div>")

    _so_html += (f"<button class='sign-submit-btn' id='ofp-sign-btn' onclick='submitSign(\"ofp\")'>"
        f"&#9998; &nbsp;Accept OFP RLS {_sign_rls} &mdash; {_sign_orig} &#8594; {_sign_dest}"
        "</button>"
        "<div id='ofp-signed-area' style='display:none;'></div>"
        "</div>")

    # &#9472;&#9472; FFD panel &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    _so_html += "<div class='spanel' id='spanel-ffd' style='padding:18px 16px 32px 16px;'>"

    _so_html += ("<div class='legal-block'>"
        "<div class='legal-title'>Fitness for Duty Statement &mdash; 14 CFR Part 117</div>"
        "EACH CREWMEMBER AFFIRMATIVELY STATES HE OR SHE IS FIT FOR DUTY IN "
        "ACCORDANCE WITH 14 CFR PART 117."
        "<div class='legal-parties'>"
        f"<div class='party-row'>"
        f"<span class='party-role'>CAP</span>"
        f"<span class='party-name'>{_sign_cpt or 'CAPTAIN'}</span>"
        f"<span class='party-phone'>{_sign_flt}</span>"
        f"</div>"
        "</div>"
        "</div>")

    _so_html += ("<div style='display:grid;grid-template-columns:1fr 1fr;"
        "gap:12px;margin-bottom:16px;'>"
        "<div><div class='sig-label'>Captain Name</div>"
        f"<input id='ffd-name' class='sig-input' type='text' autocomplete='name' "
        f"autocapitalize='characters' spellcheck='false' "
        f"placeholder='{_sign_cpt or 'LAST FIRST M'}' value=''></div>"
        "<div><div class='sig-label'>EMP #</div>"
        "<input id='ffd-cert' class='sig-input' type='text' inputmode='text' "
        "autocomplete='off' spellcheck='false' maxlength='12' "
        "placeholder='e.g. 4012345'></div></div>")

    _so_html += ("<div class='sig-label'>Signature</div>"
        "<div class='sig-canvas-wrap' id='ffd-canvas-wrap'>"
        "<canvas id='ffd-sig-canvas' height='90'></canvas>"
        "<div class='sig-placeholder' id='ffd-sig-placeholder'>Sign here with finger or mouse</div>"
        "</div>"
        "<div class='sig-actions'>"
        "<span class='sig-status' id='ffd-sig-status'></span>"
        "<button class='sig-clear' onclick='clearSig(\"ffd\")'>&#x2715; Clear</button>"
        "</div>")

    _so_html += (f"<button class='sign-submit-btn' id='ffd-sign-btn' onclick='submitSign(\"ffd\")'>"
        f"&#10003; &nbsp;Confirm Fit for Duty &mdash; {_sign_flt}"
        "</button>"
        "<div id='ffd-signed-area' style='display:none;'></div>"
        "</div>")

    _so_html += "</div>"  # overlay-inner
    _so_html += "</div>"  # close sign-overlay div

    # JS &mdash; includes signature canvas drawing logic

    html += _so_html

    # &#9472;&#9472; SETTINGS OVERLAY &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    html += """
<div id='settings-overlay' style='display:none;position:fixed;top:0;left:0;right:0;bottom:0;
  z-index:1250;background:rgba(8,28,42,0.97);overflow-y:auto;padding-top:calc(var(--topbar-h,88px) + var(--banner-h,0px));'>
  <div style='max-width:480px;margin:0 auto;padding:24px 16px 48px;'>
    <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;'>
      <span style='color:#e8f6ff;font-size:17px;font-weight:700;letter-spacing:0.5px;'>&#9881; SETTINGS</span>
      <button onclick='closeSettings()' style='background:none;border:none;color:#7ab8d4;font-size:22px;cursor:pointer;line-height:1;padding:4px 8px;'>&#10005;</button>
    </div>

    <!-- Timezone section -->
    <div style='background:linear-gradient(135deg,#0e3a52,#1a4a61);border:1px solid #1e5a7a;border-radius:10px;padding:20px;margin-bottom:16px;'>
      <div style='color:#5ab8e0;font-size:11px;font-weight:700;letter-spacing:1px;margin-bottom:14px;'>&#128336; SIMULATOR CLOCK OFFSET</div>
      <div style='color:#a0c8d8;font-size:12px;line-height:1.5;margin-bottom:16px;'>
        Shift the displayed clock and &ldquo;now&rdquo; reference by a fixed number of hours.
        Useful when flying in a different timezone in the sim.
      </div>

      <div style='display:flex;align-items:center;gap:12px;margin-bottom:14px;'>
        <button onclick='adjustTzOffset(-1)' style='width:36px;height:36px;background:#0d3347;border:1px solid #2a6a8a;border-radius:6px;color:#5ab8e0;font-size:18px;cursor:pointer;flex-shrink:0;'>&#8722;</button>
        <div style='flex:1;text-align:center;'>
          <span id='tz-display' style='color:#e8f6ff;font-size:26px;font-weight:700;letter-spacing:1px;'>no offset</span>
        </div>
        <button onclick='adjustTzOffset(+1)' style='width:36px;height:36px;background:#0d3347;border:1px solid #2a6a8a;border-radius:6px;color:#5ab8e0;font-size:18px;cursor:pointer;flex-shrink:0;'>&#43;</button>
      </div>

      <input type='range' id='tz-slider' min='-12' max='14' step='1' value='0'
        oninput='setTzOffsetFromSlider(this.value)'
        style='width:100%;accent-color:#4da8da;cursor:pointer;margin-bottom:10px;'>

      <div style='display:flex;justify-content:space-between;color:#4a7a96;font-size:10px;'>
        <span>&minus;12hr</span><span>no offset</span><span>+14hr</span>
      </div>

      <div style='margin-top:16px;display:flex;gap:10px;flex-wrap:wrap;'>
        <span style='color:#6a9ab8;font-size:11px;font-style:italic;flex:1;'>Clock &amp; time-based displays will update immediately.</span>
        <button onclick='resetTzOffset()' style='background:#0d3347;border:1px solid #2a6a8a;border-radius:5px;color:#7ab8d4;font-size:11px;padding:5px 12px;cursor:pointer;'>Reset to UTC</button>
      </div>
    </div>

  </div>
</div>
"""

    html += """
<script>
// &#9472;&#9472; Section & Tab system &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
// Each bottom-nav section has its own set of top tabs
var _activeSection = 'briefing';
var _activeTab = 'overview';

// Tab definitions per section
var _sectionTabs = {
    'ofp':      ['fw','navlog','journeylog','extrainfo'],
    'briefing': ['overview','flightbox','weather','notams','maps','atc']
};

// Default tab for each section
var _sectionDefault = {
    'ofp':      'fw',
    'briefing': 'overview'
};

// Tab display labels
var _tabLabels = {
    'fw':         'FUEL &amp; WEIGHTS',
    'navlog':     'NAVLOG',
    'journeylog': 'JOURNEY LOG',
    'extrainfo':  'EXTRA INFO',
    'overview':   'OVERVIEW',
    'flightbox':  'FLIGHTBOX',
    'weather':    'WEATHER',
    'notams':     'NOTAMS',
    'maps':       'MAPS',
    'atc':        'ATC'
};

// All overlay panel IDs
var _allPanels = ['tab-fw','tab-navlog','tab-weather','tab-notams','tab-maps',
                  'tab-flightbox','tab-journeylog','tab-extrainfo','tab-atc'];

function _hideAllPanels() {
    _allPanels.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    document.body.style.overflow = '';
}

function _buildTabBar(section) {
    var bar = document.getElementById('main-tab-bar');
    if (!bar) return;
    var tabs = _sectionTabs[section] || [];
    bar.innerHTML = '';
    tabs.forEach(function(tabId) {
        // Skip weather/notams/maps if their panels don't exist
        var panelId = 'tab-' + tabId;
        if (tabId === 'overview') panelId = null; // overview is main scroll
        if (panelId && !document.getElementById(panelId) && tabId !== 'overview') return;
        var btn = document.createElement('div');
        btn.className = 'tab' + (tabId === _activeTab ? ' active' : '');
        btn.id = 'tab-btn-' + tabId;
        btn.innerHTML = _tabLabels[tabId] || tabId.toUpperCase();
        btn.onclick = (function(t){ return function(){ switchTab(t); }; })(tabId);
        bar.appendChild(btn);
    });
}

function switchSection(section) {
    _hideAllPanels();
    // Update bottom nav highlight
    document.querySelectorAll('.bottom-nav-item').forEach(function(el) {
        el.classList.remove('active');
    });
    var bnav = document.getElementById('bnav-' + section);
    if (bnav) bnav.classList.add('active');
    _activeSection = section;
    // Switch to default tab for this section
    var defaultTab = _sectionDefault[section] || 'overview';
    _activeTab = defaultTab;
    _buildTabBar(section);
    _showTab(defaultTab);
}

function _showTab(tabId) {
    _hideAllPanels();
    if (tabId === 'overview') {
        document.body.style.overflow = '';
    } else {
        var panel = document.getElementById('tab-' + tabId);
        if (panel) {
            panel.style.display = 'block';
        }
    }
    // Update tab highlight
    document.querySelectorAll('.tab[id^="tab-btn-"]').forEach(function(el) {
        el.classList.remove('active');
    });
    var btn = document.getElementById('tab-btn-' + tabId);
    if (btn) btn.classList.add('active');
    _activeTab = tabId;
    // Re-measure the navlog fixed bar spacer whenever navlog becomes visible
    if (tabId === 'navlog' && window.nlSetSpacer) {
        setTimeout(window.nlSetSpacer, 50);
    }
}

function switchTab(tabId) {
    _showTab(tabId);
}

function _isOfpSigned() {
    if (_signed && _signed['ofp'] && _signed['ofp'].ts) return true;
    try { return !!localStorage.getItem(FLIGHT_KEY+'_sign_ofp'); } catch(e) { return false; }
}

function _unlockNavlog() {
    var bar = document.getElementById('nl-unsigned-bar');
    if (bar) bar.style.display = 'none';
    var banner = document.getElementById('nl-rls-banner');
    if (banner) banner.style.display = 'flex';
}

function closeTab(overlayId, tabName) {
    var el = document.getElementById(overlayId);
    if (el) el.style.display = 'none';
    document.body.style.overflow = '';
    // Go back to default tab of current section
    var defaultTab = _sectionDefault[_activeSection] || 'overview';
    _activeTab = defaultTab;
    _buildTabBar(_activeSection);
    document.querySelectorAll('.tab[id^="tab-btn-"]').forEach(function(t){ t.classList.remove('active'); });
    var ob = document.getElementById('tab-btn-' + defaultTab);
    if (ob) ob.classList.add('active');
}

// Legacy aliases used elsewhere in the code
function openFW()  { switchSection('ofp'); }
function closeFW() { closeTab('tab-fw', 'FUEL & WEIGHTS'); }

function fwToggleRow(row) {
    row.classList.toggle('fw-row-active');
}

function saveJourneyLog() {
    try {
        var fields = ['jl-block-off','jl-takeoff','jl-landing','jl-block-on',
                      'jl-flight-time','jl-block-time','jl-fob','jl-fuel-used',
                      'jl-fuel-rem','jl-pax','jl-remarks'];
        var data = {};
        fields.forEach(function(id){
            var el = document.getElementById(id);
            if (el) data[id] = el.value;
        });
        localStorage.setItem('av_journey_log', JSON.stringify(data));
        var btn = document.querySelector('#tab-journeylog button');
        if (btn) { btn.textContent = '\u2713 Saved'; setTimeout(function(){ btn.innerHTML = '&#10003; Save Journey Log'; }, 2000); }
    } catch(e) {}
}

function restoreJourneyLog() {
    try {
        var data = JSON.parse(localStorage.getItem('av_journey_log') || '{}');
        Object.keys(data).forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.value = data[id];
        });
    } catch(e) {}
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        _hideAllPanels();
        _activeTab = _sectionDefault[_activeSection] || 'overview';
        _buildTabBar(_activeSection);
    }
});

document.addEventListener('DOMContentLoaded', function() {
    // Wire up fw-trigger (flight profile tap) to open OFP > Fuel & Weights
    var t = document.getElementById('fw-trigger');
    if (t) t.addEventListener('click', openFW);
    // Initialize to BRIEFING section
    switchSection('briefing');
    restoreJourneyLog();

    // &#9472;&#9472; iOS keyboard fix &#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;&#9472;
    // iOS Safari: inputs inside position:fixed + overflow-y:auto panels
    // do not receive focus/keyboard reliably. The scroll container eats the
    // touch before it reaches the input. We fix this in two ways:
    //
    // 1. On touchstart (NOT touchend, NOT passive) call el.focus() immediately
    //    so it registers as a true user gesture before any scroll logic runs.
    // 2. Give every input/textarea a tabindex so iOS treats them as focusable.

    function _iosFixInputs(root) {
        var els = (root || document).querySelectorAll('input, textarea, select');
        els.forEach(function(el) {
            if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
            // Attach once using a flag
            if (el._iosFocusBound) return;
            el._iosFocusBound = true;
            el.addEventListener('touchstart', function(e) {
                // Must NOT be passive so focus() counts as user gesture
                var target = e.currentTarget;
                target.focus();
                setTimeout(function() {
                    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }, 300);
            }, { passive: false, capture: true });
        });
    }

    // Run on initial load
    _iosFixInputs(document);

    // Re-run whenever a panel becomes visible (MutationObserver on display changes)
    var _panelObserver = new MutationObserver(function(mutations) {
        mutations.forEach(function(m) {
            if (m.type === 'attributes' && m.attributeName === 'style') {
                var el = m.target;
                if (el.style && el.style.display !== 'none') {
                    // Panel just became visible &mdash; wire up any new inputs inside it
                    setTimeout(function() { _iosFixInputs(el); }, 50);
                }
            }
        });
    });
    document.querySelectorAll('[id^="tab-"], #sign-overlay, #settings-overlay').forEach(function(panel) {
        _panelObserver.observe(panel, { attributes: true, attributeFilter: ['style'] });
    });
});
</script>
"""


    html += f"""<!-- TAKEOFF ENTRY OVERLAY (direct body child for correct stacking) -->
<div id="entry-overlay">
  <div id="entry-card">
    <h2>Enter Flight Data</h2>
    <div class="entry-field">
      <label>Takeoff Time (HHMM)</label>
      <input type="text" id="input-toff" inputmode="numeric" maxlength="5" value="{sched_off}" placeholder="1430"
             oninput="updateEntryDelta('input-toff','{sched_off}','delta-toff',true)">
      <div class="entry-hint">Scheduled: {sched_off} &nbsp;<span id="delta-toff" style="font-weight:bold"></span></div>
    </div>
    <div class="entry-field">
      <label>Block Fuel (lbs)</label>
      <input type="text" id="input-fuel" inputmode="numeric" value="{plan_ramp}" placeholder="42000"
             oninput="updateEntryDelta('input-fuel','{plan_ramp}','delta-fuel',false)">
      <div class="entry-hint">Plan ramp: {plan_ramp} &nbsp;<span id="delta-fuel" style="font-weight:bold"></span></div>
    </div>
    <button id="entry-submit" onclick="applyEntryValues()">Apply</button>
    <button onclick="resetAllValues()" style="background:transparent;color:#6ab4d4;border:1px solid #2a6a8b;padding:10px 0;border-radius:4px;font-size:13px;cursor:pointer;width:100%;margin-top:10px;letter-spacing:1px;text-transform:uppercase;">&#8635; Reset</button>
  </div>
</div>

<!-- WAYPOINT POPUP (direct body child for correct stacking) -->
<div id="wp-overlay">
  <div id="wp-card">
    <div class="wp-title">CONFIRM</div>
    <div class="wp-sub" id="wp-fix-name">FIX</div>
    <div id="wp-weights-lbl">ALL WEIGHTS IN LB</div>
    <div class="wp-cols">
      <div class="wp-col">
        <label>PLND FL</label><div class="wp-plnd-val" id="wp-p-alt">&mdash;</div>
        <label>ACTUAL FL</label><input type="text" id="wp-a-alt" maxlength="5" placeholder="—">
      </div>
      <div class="wp-col">
        <label>PLND TIME</label><div class="wp-plnd-val" id="wp-p-et">&mdash;</div>
        <label>ACTUAL TIME</label><input type="text" id="wp-a-et" maxlength="5" placeholder="—">
      </div>
      <div class="wp-col">
        <label>PLND FUEL</label><div class="wp-plnd-val" id="wp-p-fuel">&mdash;</div>
        <label>ACTUAL FUEL</label><input type="text" id="wp-a-fuel" maxlength="7" placeholder="—" oninput="wpCheckFuelWarn()">
      </div>
    </div>
    <div id="wp-fuel-warn"></div>
    <div class="wp-next">
      <label>EST TIME AT NEXT WP</label>
      <input type="text" id="wp-next-et" maxlength="5" placeholder="—">
    </div>
    <div class="wp-btns">
      <button class="wp-btn-cancel" onclick="closeWp()">CANCEL</button>
      <button class="wp-btn-done" onclick="saveWp()">DONE</button>
    </div>
  </div>
</div>
"""
    html += "</body></html>\n"
    return html


if __name__ == "__main__":
    main()
    
