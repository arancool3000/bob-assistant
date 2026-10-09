"""Writes Raspberry Pi OS Lite (64-bit) onto a microSD card, so Raspberry Pi Imager is not needed.

The steps, all on this PC:
  1. read Raspberry Pi's own image list (the one Imager uses) for the newest Raspberry Pi OS Lite (64-bit)
  2. list cards: only USB / SD / MMC disks, never the disk Windows runs from, 4 GB to 512 GB
  3. after the person has typed ERASE: check it is still the same card, clear it, then download, unpack and write
     the image in one pass (the first megabyte, which holds the partition table, goes on last, so Windows does
     not grab the half-written card), check the image's sha256, then read the whole card back and check again
  4. find the card's boot drive, so bob_setup.prepare_card() can add the settings

The pure parts (finding the image, choosing disks, writing a stream to a target) are tested on any OS.
"""
import hashlib
import http.client
import json
import lzma
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

OS_LIST_URL = 'https://downloads.raspberrypi.com/os_list_imagingutility_v4.json'
OS_NAME = 'Raspberry Pi OS Lite (64-bit)'
FALLBACK = {                                         # used only if the list cannot be read
    'name': OS_NAME,
    'url': 'https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/'
           '2026-10-06-raspios-trixie-arm64-lite.img.xz',
    'extract_size': 3078619136,
    'extract_sha256': 'b8a393dc1c3701450b5fffbfe74368a98fb6eab7415ce456cc0cead886ea8780',
    'image_download_size': 550466056,
    'release_date': '2026-10-06',
}
MIN_BYTES = 3_500_000_000                            # a "4 GB" card
MAX_BYTES = 520_000_000_000                          # a "512 GB" card; anything bigger is not a microSD card
CARD_BUSES = {'USB', 'SD', 'MMC', '7', '12', '13'}   # names, or the numbers Get-Disk uses for them
HEAD = 1 << 20                                       # written last: the partition table lives here
CHUNK = 4 << 20
SECTOR = 4096                                        # a multiple of every card's sector size
GB = 1_000_000_000


class Cancelled(Exception):
    pass


class Damaged(Exception):
    pass


# ---- the image ----------------------------------------------------------------------------------------

def find_os_entry(os_list):
    """The Raspberry Pi OS Lite (64-bit) entry from Imager's list, checked."""
    def walk(items):
        for it in items or []:
            if it.get('name') == OS_NAME and it.get('url'):
                return it
            found = walk(it.get('subitems'))
            if found:
                return found
    e = walk(os_list.get('os_list'))
    if not e:
        raise ValueError('%s is not in the list' % OS_NAME)
    if not re.match(r'^https://downloads\.raspberrypi\.com/[\w./-]+\.img\.xz$', e['url']):
        raise ValueError('unexpected download address: %s' % e['url'])
    if not re.match(r'^[0-9a-f]{64}$', e.get('extract_sha256', '')) or int(e.get('extract_size', 0)) < 1 << 30:
        raise ValueError('the list has no checksum or size for the image')
    return {k: e.get(k) for k in FALLBACK}


def latest_os():
    try:
        with urllib.request.urlopen(OS_LIST_URL, timeout=20) as r:
            return find_os_entry(json.load(r))
    except Exception:
        return dict(FALLBACK)


def http_opener(url):
    """open(offset) -> a readable response starting at `offset` bytes (to carry on after a dropped connection)."""
    def open_at(offset):
        req = urllib.request.Request(url, headers={'User-Agent': 'Bob-Setup'})
        if offset:
            req.add_header('Range', 'bytes=%d-' % offset)
        r = urllib.request.urlopen(req, timeout=30)
        if offset and r.status != 206:
            r.close()
            raise Damaged('the server cannot resume the download: try again')
        return r
    return open_at


# ---- writing ------------------------------------------------------------------------------------------

class FileTarget:
    """A plain file standing in for a card (tests, and writing an .img)."""
    def __init__(self, path):
        self.f = open(path, 'r+b' if os.path.exists(path) else 'w+b')

    def write_at(self, offset, data):
        self.f.seek(offset)
        self.f.write(data)

    def read_at(self, offset, n):
        self.f.seek(offset)
        return self.f.read(n)

    def flush(self):
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self):
        self.f.close()


def _pad(b):
    return bytes(b) + b'\0' * (-len(b) % SECTOR)


def write_image(open_at, target, size, sha256, progress=None, cancel=None, retries=5):
    """Download (via open_at), unpack and write an .img.xz to target, then read it back.
    progress(stage, done, total) with stage 'write' or 'verify'. Raises on any mismatch."""
    progress = progress or (lambda *a: None)
    cancel = cancel or (lambda: False)
    dec = lzma.LZMADecompressor()
    h = hashlib.sha256()
    head = bytearray()
    pending = bytearray()
    pos = 0                       # unpacked bytes so far
    off = HEAD                    # where the next write goes
    got = 0                       # downloaded bytes so far
    tries = 0
    while not dec.eof:
        try:
            r = open_at(got)
            try:
                while not dec.eof:
                    if cancel():
                        raise Cancelled()
                    chunk = r.read(1 << 20)
                    if not chunk:
                        raise ConnectionError('the download ended early')
                    got += len(chunk)
                    out = dec.decompress(chunk)
                    h.update(out)
                    pos += len(out)
                    if pos > size:
                        raise Damaged('the image is bigger than the list says')
                    if len(head) < HEAD:
                        take = HEAD - len(head)
                        head += out[:take]
                        out = out[take:]
                    pending += out
                    if len(pending) >= CHUNK:
                        n = len(pending) - len(pending) % CHUNK
                        target.write_at(off, bytes(pending[:n]))
                        off += n
                        del pending[:n]
                    progress('write', pos, size)
            finally:
                r.close()
        except (ConnectionError, TimeoutError, urllib.error.URLError, http.client.HTTPException) as e:   # the network, not the card
            tries += 1
            if tries > retries:
                raise IOError('the download kept failing (%s): check the internet connection and try again' % e)
            time.sleep(min(30, 2 ** tries))
    if pending:
        target.write_at(off, _pad(pending))
    if pos != size or h.hexdigest() != sha256:
        raise Damaged('the downloaded image is damaged (checksum does not match): nothing was finished, try again')
    target.write_at(0, _pad(head))             # the partition table last
    target.flush()
    progress('verify', 0, size)
    v = hashlib.sha256()
    done = 0
    while done < size:
        if cancel():
            raise Cancelled()
        n = min(CHUNK, size - done)
        data = target.read_at(done, n + (-n % SECTOR))
        v.update(data[:n])
        done += n
        progress('verify', done, size)
    if v.hexdigest() != sha256:
        raise IOError('the card did not read back what was written: it may be faulty or fake, try another card')


# ---- cards on Windows ---------------------------------------------------------------------------------

def choose_disks(disks, need=MIN_BYTES):
    """The disks that may be written: cards and card readers only, never the system disk, sensible sizes."""
    out = []
    for d in disks:
        bus = str(d.get('Bus', d.get('BusType', ''))).upper()
        size = int(d.get('Size') or 0)
        if bus not in CARD_BUSES or d.get('IsBoot') or d.get('IsSystem') or d.get('IsOffline'):
            continue
        if not max(need, MIN_BYTES) <= size <= MAX_BYTES:
            continue
        out.append(d)
    return out


def describe(d):
    bus = {'7': 'USB', '12': 'SD', '13': 'MMC'}.get(str(d.get('Bus')), str(d.get('Bus')))
    return '%s (%s, %.1f GB, disk %d)' % ((d.get('FriendlyName') or 'Card').strip(), bus, int(d['Size']) / GB, int(d['Number']))


def _ps(script, timeout=60):
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                       text=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if r.returncode:
        raise IOError((r.stderr or r.stdout).strip()[-300:] or 'PowerShell failed')
    return r.stdout.strip()


_DISK_PS = ('Get-Disk | Select-Object Number,FriendlyName,SerialNumber,Size,IsBoot,IsSystem,IsOffline,'
            '@{n="Bus";e={[string]$_.BusType}} | ConvertTo-Json -Compress')


def list_disks():
    if os.name != 'nt':
        return []
    raw = _ps(_DISK_PS) or '[]'
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def same_disk(a, b):
    return all(str(a.get(k)) == str(b.get(k)) for k in ('Number', 'FriendlyName', 'SerialNumber', 'Size'))


def clear_disk(disk):
    """Check it is still the card that was chosen, then remove its partitions (diskpart clean)."""
    now = [d for d in choose_disks(list_disks()) if same_disk(d, disk)]
    if not now:
        raise IOError('the card changed or was removed: press Refresh and choose it again')
    import tempfile
    fd, path = tempfile.mkstemp(suffix='.txt')
    with os.fdopen(fd, 'w') as f:
        f.write('select disk %d\nattributes disk clear readonly noerr\nclean\nrescan\nexit\n' % int(disk['Number']))
    try:
        r = subprocess.run(['diskpart', '/s', path], capture_output=True, text=True, timeout=180,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if r.returncode:
            raise IOError('Windows could not clear the card: %s' % (r.stdout or r.stderr).strip()[-300:])
    finally:
        os.remove(path)


class DiskTarget:
    """\\\\.\\PhysicalDriveN, opened for raw writing (needs administrator rights)."""
    def __init__(self, number):
        import ctypes
        from ctypes import wintypes
        self.k = k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        for fn in (k.WriteFile, k.ReadFile):
            fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        k.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
        k.FlushFileBuffers.argtypes = k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ct, self.wt = ctypes, wintypes
        GENERIC_RW, SHARE_RW, OPEN_EXISTING, WRITE_THROUGH = 0xC0000000, 3, 3, 0x80000000
        h = k.CreateFileW(r'\\.\PhysicalDrive%d' % int(number), GENERIC_RW, SHARE_RW, None, OPEN_EXISTING, WRITE_THROUGH, None)
        if not h or h == wintypes.HANDLE(-1).value:
            raise OSError('could not open the card (error %d): run Bob Setup as administrator' % ctypes.get_last_error())
        self.h = h

    def _seek(self, offset):
        if not self.k.SetFilePointerEx(self.h, offset, None, 0):
            raise OSError('seek failed (error %d)' % self.ct.get_last_error())

    def write_at(self, offset, data):
        self._seek(offset)
        n = self.wt.DWORD()
        buf = self.ct.create_string_buffer(bytes(data), len(data))
        if not self.k.WriteFile(self.h, buf, len(data), self.ct.byref(n), None) or n.value != len(data):
            raise OSError('writing the card failed (error %d)' % self.ct.get_last_error())

    def read_at(self, offset, size):
        self._seek(offset)
        n = self.wt.DWORD()
        buf = self.ct.create_string_buffer(size)
        if not self.k.ReadFile(self.h, buf, size, self.ct.byref(n), None):
            raise OSError('reading the card failed (error %d)' % self.ct.get_last_error())
        return buf.raw[:n.value]

    def flush(self):
        self.k.FlushFileBuffers(self.h)

    def close(self):
        if self.h:
            self.k.CloseHandle(self.h)
            self.h = None


def boot_drive_of(number, wait=60):
    """The drive letter Windows gives the card's boot partition, once it has noticed the new partitions."""
    n = int(number)
    end = time.time() + wait
    while time.time() < end:
        try:
            _ps('Update-Disk -Number %d' % n)
            letter = _ps('(Get-Partition -DiskNumber %d -PartitionNumber 1).DriveLetter' % n)
            if not re.match(r'^[A-Z]$', letter):
                _ps('Add-PartitionAccessPath -DiskNumber %d -PartitionNumber 1 -AssignDriveLetter' % n)
                letter = _ps('(Get-Partition -DiskNumber %d -PartitionNumber 1).DriveLetter' % n)
            root = '%s:\\' % letter
            if re.match(r'^[A-Z]$', letter) and os.path.exists(os.path.join(root, 'config.txt')):
                return root
        except (IOError, OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    raise IOError('the card was written, but Windows did not show its boot drive: unplug it, plug it back in, '
                  'then use "Save settings only"')


def eject(root):
    """Best effort: the same as "Eject" in File Explorer."""
    try:
        _ps("(New-Object -ComObject Shell.Application).Namespace(17).ParseName('%s').InvokeVerb('Eject')" % root.rstrip('\\'))
        return True
    except Exception:
        return False


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
