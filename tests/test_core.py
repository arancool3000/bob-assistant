import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'setup-windows'))
TMP = tempfile.mkdtemp()
os.environ['BOB_ETC'] = os.path.join(TMP, 'etc')
os.environ['BOB_STATE'] = os.path.join(TMP, 'state')
os.environ['BOB_SKILLS'] = os.path.join(TMP, 'skills')
os.environ['BOB_SKILL_SOCK'] = os.path.join(TMP, 's.sock')
os.environ['BOB_HOST_INPROC'] = '1'
os.environ['BOB_UPDATE_STATE'] = os.path.join(TMP, 'upd')

from bob import audio, config, skillhost, skills, tools, updater, wake  # noqa: E402
import bob_setup  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

os.makedirs(os.environ['BOB_SKILLS'], exist_ok=True)
_ready = threading.Event()
threading.Thread(target=skillhost.serve, args=(os.environ['BOB_SKILLS'], os.environ['BOB_SKILL_SOCK'], _ready), daemon=True).start()
_ready.wait(5)


def test_password_hash_roundtrip():
    h = config.hash_password('correct horse')
    assert h.startswith('pbkdf2$') and 'correct horse' not in h
    assert config.check_password('correct horse', h)
    assert not config.check_password('wrong', h)


def test_config_defaults_and_save():
    cfg = config.load()
    assert cfg['wake_phrase'] == 'hey bob'
    cfg['town'] = 'Leeds'
    config.save(cfg)
    assert config.load()['town'] == 'Leeds'


def test_secrets_file_is_private():
    config.save_secrets({'gemini_api_key': 'k', 'web_password': ''})
    assert oct(os.stat(config.SECRETS_FILE).st_mode)[-3:] == '600'
    assert config.load_secrets()['gemini_api_key'] == 'k'


def test_wake_decision():
    w = [{'word': 'hey', 'conf': 0.9}, {'word': 'bob', 'conf': 0.8}]
    assert wake.decide(w, 'hey bob', 55)
    assert not wake.decide([{'word': 'hey', 'conf': 0.9}, {'word': 'bob', 'conf': 0.5}], 'hey bob', 55)
    assert not wake.decide([{'word': '[unk]', 'conf': 1}], 'hey bob', 90)
    assert wake.min_confidence(100) < wake.min_confidence(0)


GOOD = '''SKILL = {"name": "x", "description": "Say hello.", "parameters": {"type": "OBJECT", "properties": {"who": {"type": "STRING"}}}}

def run(who="you"):
    return {"say": "hello " + who}
'''


def test_skill_validate_rejects_bad():
    for code, why in [('def run(): pass', 'SKILL'), ('SKILL = {"description": "d"}', 'run('), ('def (', 'parse')]:
        try:
            skills.validate('ok_name', code)
            assert False, 'should have failed'
        except ValueError as e:
            assert why in str(e)
    try:
        skills.validate('Bad-Name', GOOD)
        assert False
    except ValueError:
        pass


def test_skill_create_needs_yes_then_runs():
    # confirmed=true on the first call is not enough: the exact code must have been offered first
    r = skills.handle('create_skill', {'name': 'greet', 'code': GOOD, 'confirmed': True})
    assert r.get('needs_confirmation') and 'Shall I install' in r['say']
    assert not os.path.exists(os.path.join(os.environ['BOB_SKILLS'], 'greet.py'))
    r = skills.handle('create_skill', {'name': 'greet', 'code': GOOD, 'confirmed': True})
    assert r.get('needs_confirmation'), 'too soon after the offer'
    time.sleep(2.1)
    r = skills.handle('create_skill', {'name': 'greet', 'code': GOOD + '\n', 'confirmed': True})
    assert r.get('needs_confirmation'), 'different code from what was offered'
    time.sleep(2.1)
    r = skills.handle('create_skill', {'name': 'greet', 'code': GOOD, 'confirmed': True})
    assert r['ok'], r
    out = skills.handle('run_skill', {'name': 'greet', 'args_json': '{"who": "Sam"}'})
    assert out == {'say': 'hello Sam'}, out
    assert [d['name'] for d in skills.declarations()] == ['skill_greet']


def test_broken_skill_cannot_crash_bob():
    skills.save('boom', GOOD.replace('return {"say": "hello " + who}', 'raise RuntimeError("nope")'))
    r = skills.run('boom', {})
    assert 'RuntimeError' in r['error']


def test_skill_state_lasts_between_calls():
    skills.save('counter', '''SKILL = {"description": "Count.", "parameters": {"type": "OBJECT", "properties": {}}}
N = [0]

def run():
    N[0] += 1
    return {"n": N[0]}
''')
    assert skills.run('counter', {})['n'] == 1
    assert skills.run('counter', {})['n'] == 2         # the module (and e.g. a gpiozero LED) stays alive


def test_bad_schema_and_names_are_refused():
    bad = GOOD.replace('{"type": "STRING"}', '"text"')
    try:
        skills.validate('x_bad', bad)
        assert False
    except ValueError as e:
        assert 'type' in str(e)
    for name in ('../etc/passwd', '/abs', 'Upper'):
        assert 'error' in skills.handle('show_skill', {'name': name}) or skills.source(name) is None


def test_broken_skill_file_does_not_stop_declarations():
    with open(os.path.join(os.environ['BOB_SKILLS'], 'junk.py'), 'w') as f:
        f.write('SKILL = {"description": 5}\ndef run(): pass\n')
    good, bad = skills.load_all()
    assert any(b['name'] == 'junk' for b in bad)
    skills.declarations()                               # must not raise


def test_wake_phrase_checks_and_device_safety():
    assert wake.check_phrase('', '/nope') and wake.check_phrase('hey, b0b', '/nope')
    assert wake.check_phrase('hey bob', '/nope') is None
    assert not wake.decide([{'word': 'hey', 'conf': 1}], '', 55)
    assert audio.safe_device('plughw:CARD=USB,DEV=0') == 'plughw:CARD=USB,DEV=0'
    assert audio.safe_device('|rm -rf /') == 'default' and audio.safe_device('file:/tmp/x') == 'default'


def test_timers():
    t = tools.Tools(config.load())
    r = t.call('set_timer', {'minutes': 1, 'name': 'tea'})
    assert r['ok']
    assert t.call('list_timers', {})['timers'][0]['name'] == 'tea'
    assert t.call('cancel_timer', {'name': 'tea'})['cancelled'] == 1


def test_unknown_tool_is_an_error_not_a_crash():
    assert 'error' in tools.Tools(config.load()).call('nope', {})


def test_updater_picks_newest_release():
    assert updater.newest_tag(['v0.1.0', 'v0.10.0', 'v0.9.9', 'junk', 'v1.0.0-rc1']) == 'v0.10.0'
    assert updater.version_key('v1.2.3') == (1, 2, 3)


def test_setup_app_ssh_validation_does_not_need_wifi():
    assert bob_setup.validate_ssh({'username': 'pi', 'password': 'x', 'gemini_api_key': 'k', 'web_password': 'webpass'}) == []


def test_setup_app_validation_and_files():
    d = {'hostname': 'bob', 'username': 'pi', 'password': 'p"a ss\\w0rd', 'wifi_ssid': 'Home "Net"', 'wifi_password': "it's-secret",
         'country': 'GB', 'gemini_api_key': 'AIza-x', 'web_password': 'webpass', 'name': 'Bob', 'wake_phrase': 'hey bob',
         'town': 'London', 'voice': 'Charon'}
    assert bob_setup.validate(d) == []
    assert bob_setup.validate(dict(d, hostname='Bad Name', password='short'))
    files = bob_setup.cloud_init_files(d)
    assert files['user-data'].startswith('#cloud-config')
    try:
        import yaml
    except ImportError:
        return
    u = yaml.safe_load(files['user-data'])
    n = yaml.safe_load(files['network-config'])
    assert u['chpasswd']['users'][0]['password'] == d['password']
    assert n['network']['wifis']['wlan0']['access-points'][d['wifi_ssid']]['password'] == d['wifi_password']
    assert json.loads(u['write_files'][0]['content'])['gemini_api_key'] == 'AIza-x'
    assert any(w['path'] == '/usr/local/sbin/bob-firstboot.sh' for w in u['write_files'])
    assert 'Restart=on-failure' in [w for w in u['write_files'] if w['path'].endswith('.service')][0]['content']


def test_setup_app_writes_old_style_card():
    root = tempfile.mkdtemp()
    open(os.path.join(root, 'cmdline.txt'), 'w').write('console=tty1 root=PARTUUID=x rootwait\n')
    open(os.path.join(root, 'config.txt'), 'w').write('')
    d = {'hostname': 'bob', 'username': 'pi', 'password': 'password1', 'wifi_ssid': 'Home', 'wifi_password': 'secret123',
         'gemini_api_key': 'k', 'web_password': 'webpass', 'wake_phrase': 'hey bob'}
    done = bob_setup.prepare_card(root, d)
    assert 'wrote firstrun.sh' in done
    assert 'systemd.run=/boot/firmware/firstrun.sh' in open(os.path.join(root, 'cmdline.txt')).read()
    assert os.path.exists(os.path.join(root, 'ssh'))


def _xz_image(n=5 << 20):
    import hashlib
    import lzma
    img = os.urandom(n)
    return img, lzma.compress(img), hashlib.sha256(img).hexdigest()


def test_flasher_finds_the_image_in_imagers_list():
    import flasher
    lst = {'os_list': [{'name': 'Other', 'subitems': [dict(flasher.FALLBACK)]}]}
    assert flasher.find_os_entry(lst)['extract_sha256'] == flasher.FALLBACK['extract_sha256']
    bad = dict(flasher.FALLBACK, url='https://evil.example/x.img.xz')
    try:
        flasher.find_os_entry({'os_list': [bad]})
        assert False, 'a download from anywhere else must be refused'
    except ValueError:
        pass


def test_flasher_only_offers_cards():
    import flasher
    disks = [
        {'Number': 0, 'Bus': 'NVMe', 'Size': 512e9, 'IsBoot': True, 'IsSystem': True},
        {'Number': 1, 'Bus': 'USB', 'Size': 32e9, 'IsBoot': False, 'IsSystem': False, 'FriendlyName': 'SD Card'},
        {'Number': 2, 'Bus': '12', 'Size': 64e9},
        {'Number': 3, 'Bus': 'USB', 'Size': 2e12},                  # a USB hard drive: too big to be a card
        {'Number': 4, 'Bus': 'USB', 'Size': 2e9},                   # too small
        {'Number': 5, 'Bus': 'SATA', 'Size': 64e9},
        {'Number': 6, 'Bus': 'USB', 'Size': 32e9, 'IsSystem': True},
    ]
    assert [d['Number'] for d in flasher.choose_disks(disks)] == [1, 2]
    assert 'SD Card' in flasher.describe(disks[1]) and '32.0 GB' in flasher.describe(disks[1])


def test_flasher_writes_resumes_and_verifies():
    import io
    import flasher
    img, xz, sha = _xz_image()
    calls = []

    def open_at(off):                     # drops the connection once, part-way through
        calls.append(off)
        if len(calls) == 1:
            class Drop(io.BytesIO):
                def read(self, n=-1):
                    if self.tell() > len(xz) // 2:
                        raise ConnectionResetError('dropped')
                    return super().read(min(n, 65536))
            return Drop(xz)
        return io.BytesIO(xz[off:])
    path = os.path.join(TMP, 'card.img')
    flasher.time.sleep, slept = (lambda s: None), flasher.time.sleep
    try:
        t = flasher.FileTarget(path)
        stages = set()
        flasher.write_image(open_at, t, len(img), sha, lambda st, d, n: stages.add(st))
        t.close()
    finally:
        flasher.time.sleep = slept
    assert len(calls) == 2 and calls[1] > 0, 'the download carried on where it stopped'
    assert open(path, 'rb').read()[:len(img)] == img and stages == {'write', 'verify'}


def test_flasher_refuses_a_damaged_image_and_leaves_no_partition_table():
    import io
    import flasher
    img, xz, _sha = _xz_image(3 << 20)
    path = os.path.join(TMP, 'card2.img')
    t = flasher.FileTarget(path)
    try:
        flasher.write_image(lambda off: io.BytesIO(xz[off:]), t, len(img), '0' * 64)
        assert False
    except flasher.Damaged:
        pass
    t.close()
    assert open(path, 'rb').read(flasher.HEAD).strip(b'\0') == b'', 'the first megabyte is only written once all is well'


def test_flasher_cancel_stops():
    import io
    import flasher
    img, xz, sha = _xz_image(3 << 20)
    t = flasher.FileTarget(os.path.join(TMP, 'card3.img'))
    try:
        flasher.write_image(lambda off: io.BytesIO(xz[off:]), t, len(img), sha, cancel=lambda: True)
        assert False
    except flasher.Cancelled:
        pass
    t.close()


START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


def test_helper_accepts_only_a_chess_position():
    from bob import helper
    ok = helper.check_job({'t': 'job', 'j': 'a1b2c3d4-0000', 'fen': START, 'ms': 99999, 'elo': 9999})
    assert ok == {'j': 'a1b2c3d4-0000', 'fen': START, 'ms': helper.MS_MAX, 'elo': helper.ELO_MAX}
    for bad in ({'t': 'run', 'cmd': 'rm -rf /'}, {'t': 'job', 'j': 'x', 'fen': START},
                {'t': 'job', 'j': 'a1b2c3d4', 'fen': START + '; quit'}, {'t': 'job', 'j': 'a1b2c3d4', 'fen': 'go infinite'},
                {'t': 'job', 'j': 'a1b2c3d4', 'fen': START.replace(' w ', ' w\nquit\n')}, 'job', None):
        assert helper.check_job(bad) is None, bad


def test_helper_plays_through_uci():
    import asyncio
    from bob import helper
    eng = helper.Engine([sys.executable, os.path.join(ROOT, 'tests', 'fake_stockfish.py')])
    mv, depth = asyncio.run(eng.move(START, 100, 1500))
    assert mv == 'e2e4' and depth == 7


def test_helper_identity_is_private_and_kept():
    from bob import helper
    helper.STATE = os.path.join(TMP, 'helper')
    helper.IDENTITY = os.path.join(helper.STATE, 'identity.json')
    helper.STATUS = os.path.join(helper.STATE, 'status.json')
    a = helper.identity()
    assert helper.identity() == a and oct(os.stat(helper.IDENTITY).st_mode & 0o777) == '0o600'
    helper.write_status(connected=True, link_code='ABCD2345')
    assert helper.read_status()['link_code'] == 'ABCD2345'


def test_setup_app_leaves_kindlehub_helper_off_unless_ticked():
    assert json.loads(bob_setup.firstboot_json(bob_setup.DEMO))['kindlehub_helper'] is False
    assert json.loads(bob_setup.firstboot_json(dict(bob_setup.DEMO, kindlehub_helper=True)))['kindlehub_helper'] is True
    assert json.loads(bob_setup.firstboot_json(dict(bob_setup.DEMO, kindlehub_helper='yes')))['kindlehub_helper'] is False
