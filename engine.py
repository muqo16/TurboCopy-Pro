import os
import sys
import time
import math
import ctypes
from ctypes import wintypes
import subprocess
import threading
import queue
import shutil
import hashlib
import tarfile
from concurrent.futures import ThreadPoolExecutor
import psutil
from typing import Callable, Optional, Dict, Any, List, Union

# Win32 Constants for Direct Hardware DMA (Unbuffered Sequential I/O)
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
CREATE_ALWAYS = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_WRITE_THROUGH = 0x80000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# Win32 error codes worth naming, so a failure says what happened instead of only
# "Yazma hatasi". The two sets below decide whether an error stops the whole job or
# only skips one file: a disk that has left the bus, or filled up, will fail for every
# remaining file, and silently skipping thousands of them is the behaviour this exists
# to prevent.
WIN_ERROR_TEXTS = {
    5: "Erisim engellendi",
    6: "Aygit tanitici gecersiz (surucu koptu)",
    19: "Ortam yazma korumali",
    21: "Aygit hazir degil",
    23: "Veri hatasi (CRC) - yuzey veya kablo hatasi",
    29: "Yazma hatasi (aygit yanit vermedi)",
    30: "Okuma hatasi (aygit yanit vermedi)",
    31: "Aygit duzgun calismiyor",
    39: "Diskte yer kalmadi",
    55: "Aygit artik sistemde yok",
    64: "Ag baglantisi koptu",
    112: "Hedef diskte yeterli yer yok",
    433: "Boyle bir aygit yok (surucu cikarildi)",
    1005: "Birim taninamadi (dosya sistemi bozuk)",
    1112: "Surucude ortam yok",
    1117: "Aygit G/C hatasi (surucu koptu veya ariza)",
    1167: "Aygit baglanti durumda degil",
}

# Errors meaning the volume itself is gone or unusable - the job must stop.
DEVICE_LOST_CODES = {6, 21, 29, 30, 31, 55, 64, 433, 1005, 1112, 1117, 1167}
# Errors meaning the destination is out of space - also pointless to continue.
DISK_FULL_CODES = {39, 112}
# Codes that are either one bad path or the whole volume going away; which one it is
# gets decided by probing the destination root.
AMBIGUOUS_PATH_CODES = {2, 3, 15, 53, 67}


def win_error_text(code: int) -> str:
    """Readable form of a Win32 error code, always carrying the raw number."""
    label = WIN_ERROR_TEXTS.get(code)
    return f"{label} (Win32 hata {code})" if label else f"Win32 hata {code}"


FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
# The only attributes worth carrying over. Writing attributes costs another open of the
# destination, so it is skipped entirely unless the source actually has one set.
COPYABLE_ATTRIBUTES = FILE_ATTRIBUTE_READONLY | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM

MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
MEM_RELEASE = 0x00008000
PAGE_READWRITE = 0x04
FileEndOfFileInfo = 6


class FILE_END_OF_FILE_INFO(ctypes.Structure):
    _fields_ = [("EndOfFile", ctypes.c_longlong)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.SetFileInformationByHandle.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
kernel32.VirtualAlloc.argtypes = [
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAlloc.restype = wintypes.LPVOID
kernel32.VirtualFree.argtypes = [
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFree.restype = wintypes.BOOL
kernel32.GetFileTime.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
]
kernel32.GetFileTime.restype = wintypes.BOOL
kernel32.SetFileTime.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
]
kernel32.SetFileTime.restype = wintypes.BOOL
kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
kernel32.GetFileAttributesW.restype = wintypes.DWORD
kernel32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
kernel32.SetFileAttributesW.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL


IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
StorageDeviceProperty = 0
StorageDeviceSeekPenaltyProperty = 7
PropertyStandardQuery = 0
BusTypeUsb = 0x07


class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", wintypes.DWORD),
        ("QueryType", wintypes.DWORD),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("IncursSeekPenalty", ctypes.c_ubyte),
    ]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", wintypes.DWORD),
        ("ProductIdOffset", wintypes.DWORD),
        ("ProductRevisionOffset", wintypes.DWORD),
        ("SerialNumberOffset", wintypes.DWORD),
        ("BusType", wintypes.DWORD),
        ("RawPropertiesLength", wintypes.DWORD),
        ("RawDeviceProperties", ctypes.c_ubyte * 1),
    ]


_disk_profile_cache: Dict[str, str] = {}


def _query_storage_property(h_vol, property_id: int, out_struct):
    query = STORAGE_PROPERTY_QUERY()
    query.PropertyId = property_id
    query.QueryType = PropertyStandardQuery
    returned = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(
        h_vol,
        IOCTL_STORAGE_QUERY_PROPERTY,
        ctypes.byref(query),
        ctypes.sizeof(query),
        ctypes.byref(out_struct),
        ctypes.sizeof(out_struct),
        ctypes.byref(returned),
        None,
    )
    return bool(ok) and returned.value > 0


def detect_disk_profile(path: str) -> str:
    """
    Classifies the volume behind `path` as 'ssd', 'hdd', 'usb_hdd', 'network' or 'unknown'.

    Uses IOCTL_STORAGE_QUERY_PROPERTY, which needs no administrator rights: a seek
    penalty means rotating media, so the copy must not flood it with parallel writes.
    """
    if not path:
        return "unknown"
    if path.startswith(("\\\\", "//")):
        return "network"

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive or len(drive) < 2:
        return "unknown"
    drive = drive.rstrip("\/").upper()

    cached = _disk_profile_cache.get(drive)
    if cached:
        return cached

    profile = "unknown"
    h_vol = kernel32.CreateFileW(
        f"\\\\.\\{drive}", 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if h_vol and h_vol != INVALID_HANDLE_VALUE:
        try:
            seek = DEVICE_SEEK_PENALTY_DESCRIPTOR()
            if _query_storage_property(h_vol, StorageDeviceSeekPenaltyProperty, seek):
                profile = "hdd" if seek.IncursSeekPenalty else "ssd"

            dev = STORAGE_DEVICE_DESCRIPTOR()
            if _query_storage_property(h_vol, StorageDeviceProperty, dev):
                if dev.BusType == BusTypeUsb:
                    # USB enclosures often hide the seek penalty flag; stay conservative.
                    profile = "usb_hdd" if profile in ("hdd", "unknown") else "usb_ssd"
        finally:
            kernel32.CloseHandle(h_vol)

    _disk_profile_cache[drive] = profile
    return profile


def recommended_threads(profile: str, requested: int) -> int:
    """
    Caps the worker count to what the target medium can actually absorb.

    Measured on a 400 MB / 6635 small-file set copied to a USB mechanical disk:

        workers:   1       4        8        16
        time:      18.8s   254.5s   127.7s   141.3s

    Parallel writes make the head seek between streams, so on rotating media a
    single stream is ~13x faster. Flash has no seek penalty and needs the
    parallelism to stay saturated, so it keeps the full worker count.
    """
    caps = {
        "ssd": requested,
        "usb_ssd": max(4, min(requested, 16)),
        "network": max(4, min(requested, 16)),
        "hdd": 1,
        "usb_hdd": 1,
        # Detection failed: a misdetected HDD loses far more from too many workers
        # than a misdetected SSD loses from too few, so stay low.
        "unknown": max(1, min(requested, 2)),
    }
    return caps.get(profile, max(1, min(requested, 2)))


PROFILE_LABELS = {
    "ssd": "SSD",
    "usb_ssd": "USB SSD",
    "hdd": "Mekanik disk (HDD)",
    "usb_hdd": "USB mekanik disk",
    "network": "Ag suruculi",
    "unknown": "Bilinmeyen disk",
}



def format_bytes(size: float) -> str:
    """Format bytes into human readable format (B, KB, MB, GB, TB)."""
    if size < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} {units[idx]}"
    return f"{size:.2f} {units[idx]}"


def format_time(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "--:--"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def get_path_size(path: str) -> tuple[int, int]:
    """Calculate total size in bytes and number of files using fast os.scandir (zero extra stat calls on Windows)."""
    if not os.path.exists(path):
        return 0, 0
    if os.path.isfile(path):
        try:
            return os.path.getsize(path), 1
        except Exception:
            return 0, 1

    total_size = 0
    file_count = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total_size += entry.stat(follow_symlinks=False).st_size
                            file_count += 1
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            continue
    return total_size, file_count


def calculate_hash(path: str, cancel_flag: threading.Event, block_size: int = 16 * 1024 * 1024) -> Optional[str]:
    """Fast MD5 checksum using 16MB blocks."""
    hasher = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while True:
                if cancel_flag and cancel_flag.is_set():
                    return None
                buf = f.read(block_size)
                if not buf:
                    break
                hasher.update(buf)
        return hasher.hexdigest()
    except Exception:
        return None


def is_file_identical(src: str, dst: str) -> bool:
    """Checks if source and destination files have same size and modification time."""
    try:
        if not os.path.exists(dst):
            return False
        s_stat = os.stat(src)
        d_stat = os.stat(dst)
        if s_stat.st_size != d_stat.st_size:
            return False
        if abs(s_stat.st_mtime - d_stat.st_mtime) > 2.0:
            return False
        return True
    except Exception:
        return False


class PackageCancelled(Exception):
    """Raised inside the tar stream to unwind out of it when the job is stopped."""


class PackageReader:
    """
    Source-side wrapper the tar writer pulls through, so one long stream still has
    live progress, pause, cancel and the speed limit.

    It always returns exactly as many bytes as asked for until the member is done,
    padding with zeros if the source turns out shorter or unreadable: a tar member
    whose data is short makes the rest of the archive unreadable, so a single bad
    file is recorded as failed rather than allowed to corrupt everything after it.
    """

    def __init__(self, job, fh, size, base_offset, global_total, path):
        self.job = job
        self.fh = fh
        self.remaining = size
        self.base = base_offset
        self.total = global_total
        self.path = path
        self.read_bytes = 0

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.remaining
        size = min(size, self.remaining)
        if size <= 0:
            return b""
        if self.job.cancel_event.is_set():
            raise PackageCancelled()
        self.job._wait_if_paused()

        out = bytearray()
        while len(out) < size:
            want = size - len(out)
            try:
                chunk = self.fh.read(want)
            except OSError as exc:
                self.job._note_os_error(exc, self.path, writing=False)
                self.job.failed_items.append({
                    "source": self.path, "destination": self.job.archive_path or "",
                    "error": f"Okunamadi, pakete bos yazildi: {exc}",
                })
                self.job.log(f"[UYARI] {self.path} okunamadi, pakete bos olarak yazildi.", "warning")
                chunk = b""
            if not chunk:
                # Short or unreadable source: pad so the archive stays well formed.
                out.extend(b"\x00" * want)
                break
            out.extend(chunk)

        self.remaining -= len(out)
        self.read_bytes += len(out)
        self.job._current_bytes = self.base + self.read_bytes
        self.job._throttle(len(out))
        self.job._report(self.job._current_bytes, self.total, self.job._current_label)
        return bytes(out)


class CopyJob:
    SMALL_FILE_LIMIT = 8 * 1024 * 1024  # below this, parallel buffered copy beats a single DMA stream
    # Destination-loss watchdog: how often it looks, and how long progress has to be
    # frozen before the volume is worth probing at all.
    WATCHDOG_INTERVAL = 5.0
    WATCHDOG_STALL_SECONDS = 25.0
    # One sequential stream is the whole point of package mode: big blocks, no seeks.
    PACK_CHUNK = 1024 * 1024
    DMA_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB hardware-aligned DMA block (updates UI ~33x/sec, zero RAM cache stalls)

    def __init__(
        self,
        sources: Union[str, List[str]],
        destination: str,
        mode: str = "turbo_kernel",  # 'turbo_kernel', 'robocopy', 'stream'
        overwrite: bool = True,
        verify: bool = False,
        sync_mode: bool = False,
        speed_limit_mbps: Optional[int] = None,
        threads: int = 8,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_finished: Optional[Callable[[bool, str, Dict[str, Any]], None]] = None,
        precomputed_items: Optional[List[Dict[str, Any]]] = None,
    ):
        if isinstance(sources, (list, tuple)):
            self.sources = [os.path.abspath(s) for s in sources if s and os.path.exists(s)]
        else:
            self.sources = [os.path.abspath(sources)] if sources and os.path.exists(sources) else []

        self.destination = os.path.abspath(destination)
        self.mode = mode
        self.overwrite = overwrite
        self.verify = verify
        self.sync_mode = sync_mode
        self.speed_limit_mbps = speed_limit_mbps
        self.threads = threads
        self.disk_profile = "unknown"
        self.effective_threads = threads
        self.on_progress = on_progress
        self.on_log = on_log
        self.on_finished = on_finished
        self.precomputed_items = precomputed_items or []

        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.is_paused = False
        self.is_running = False
        self._current_proc = None
        self.failed_items = []

        self._dma_buf = None
        self._unflushed_small_bytes = 0
        self._total_paused_time = 0.0
        self._throttle_start = 0.0
        self._throttle_bytes = 0
        self._prog_last_time = 0.0
        self._prog_last_bytes = 0
        self._speed_hist = []
        self._last_speed = 0.0
        self._current_bytes = 0
        self._global_total_bytes = 0
        self._current_label = "Hazirlaniyor..."
        self._report_lock = threading.Lock()

        # Set when the job was stopped by a failure rather than by the user; carries the
        # message shown in place of "iptal edildi".
        self.fatal_error = None
        self.device_lost = False
        self._dest_root = None
        # Set by package mode to the .tar it wrote, so the UI can name it afterwards.
        self.archive_path = None

    def log(self, message: str, level: str = "info"):
        if self.on_log:
            self.on_log(message, level)

    def pause(self):
        """Pause copy operation and broadcast state immediately."""
        if not self.is_paused:
            self.is_paused = True
            self.pause_event.clear()
            if self._current_proc and self._current_proc.poll() is None:
                try:
                    p = psutil.Process(self._current_proc.pid)
                    p.suspend()
                except Exception:
                    pass
            self.log("[DURAKLATILDI] Kopyalama gecici olarak duraklatildi.", "warning")
            self._report(self._current_bytes, self._global_total_bytes, self._current_label, force=True)

    def resume(self):
        """Resume paused copy operation and broadcast state immediately."""
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            if self._current_proc and self._current_proc.poll() is None:
                try:
                    p = psutil.Process(self._current_proc.pid)
                    p.resume()
                except Exception:
                    pass
            self.log("[DEVAM] Kopyalama kaldigi yerden devam ediyor.", "info")
            self._report(self._current_bytes, self._global_total_bytes, self._current_label, force=True)

    def cancel(self):
        """Cancel copy operation safely."""
        self.cancel_event.set()
        self.pause_event.set()
        if self._current_proc and self._current_proc.poll() is None:
            try:
                p = psutil.Process(self._current_proc.pid)
                if self.is_paused:
                    p.resume()
                p.kill()
            except Exception:
                pass
        self.log("[IPTAL] Kopyalama kullanici tarafindan iptal ediliyor...", "warning")

    def _abort_fatal(self, message: str, device_lost: bool = True):
        """
        Stops the entire job. For failures that will repeat for every remaining file,
        where skipping them one at a time only hides the real problem.
        """
        if self.fatal_error:
            return
        self.fatal_error = message
        self.device_lost = device_lost
        self.log(f"[DURDURULDU] {message}", "error")
        self.cancel_event.set()
        self.pause_event.set()
        self.is_paused = False
        if self._current_proc and self._current_proc.poll() is None:
            try:
                p = psutil.Process(self._current_proc.pid)
                p.resume()
                p.kill()
            except Exception:
                pass

    def _destination_online(self) -> bool:
        """
        Whether the destination volume still answers. One stat on its root is enough:
        a USB disk that has left the bus makes this fail immediately.
        """
        root = self._dest_root or self.destination
        try:
            return os.path.isdir(root)
        except OSError:
            return False

    def _device_watchdog_loop(self):
        """
        Guards against the destination disk disappearing mid-copy - a USB disk dropping
        off the bus, a share going away. Windows leaves the queued writes pending
        indefinitely in that case, so without this the job just sits there with no error
        at all; one such stall cost five hours of waiting before anyone could tell.

        Frozen progress alone is not proof: a slow medium, or one enormous file, looks
        the same from here. So the volume has to be genuinely unreachable before
        anything is aborted, and a volume that still answers only re-arms the timer.
        """
        last_bytes = -1
        frozen_since = 0.0

        while self.is_running and not self.cancel_event.is_set():
            time.sleep(self.WATCHDOG_INTERVAL)
            if not self.is_running or self.cancel_event.is_set():
                return
            if self.is_paused:
                frozen_since = 0.0
                continue

            current = self._current_bytes
            if current != last_bytes:
                last_bytes = current
                frozen_since = 0.0
                continue

            now = time.time()
            if frozen_since == 0.0:
                frozen_since = now
                continue
            if now - frozen_since < self.WATCHDOG_STALL_SECONDS:
                continue

            if self._destination_online():
                # Still there, only slow: keep watching rather than killing a valid copy.
                frozen_since = now
                continue

            self._abort_fatal(
                f"Hedef disk sistemden koptu: {self._dest_root or self.destination} "
                f"artik erisilemiyor. O ana kadar {format_bytes(self._current_bytes)} "
                f"aktarilmisti; diskin kablosunu ve beslemesini kontrol edin."
            )
            return

    def _note_os_error(self, exc: BaseException, path: str, writing: bool):
        """
        Same check as _note_win_error for failures that surface as an OSError - the
        buffered small-file path and os.makedirs go through here. Without it a lost
        disk would be reported once per remaining file and the job would still claim
        to have finished.
        """
        code = getattr(exc, "winerror", None)
        if isinstance(code, int):
            self._note_win_error(code, path, writing)
            return
        # Python's own file objects raise FileNotFoundError and friends with an errno
        # and no Win32 code at all, so fall back to the check that settles it either
        # way: a write that failed while the destination root no longer answers means
        # the volume is gone, whatever the code said.
        if writing and not self._destination_online():
            self._abort_fatal(
                f"Hedef disk sistemden koptu: {self._dest_root or self.destination} "
                f"artik erisilemiyor ({exc}). "
                f"O ana kadar {format_bytes(self._current_bytes)} aktarilmisti."
            )

    def _note_win_error(self, code: int, path: str, writing: bool):
        """Escalates a device-level Win32 error into a job-stopping one."""
        side = "Hedef" if writing else "Kaynak"
        if code in DEVICE_LOST_CODES:
            self._abort_fatal(
                f"{side} disk yanit vermiyor - {win_error_text(code)}. "
                f"Son denenen dosya: {path}"
            )
        elif code in DISK_FULL_CODES:
            self._abort_fatal(
                f"Hedef diskte yer kalmadi - {win_error_text(code)}. "
                f"Son denenen dosya: {path}",
                device_lost=False,
            )
        elif writing and code in AMBIGUOUS_PATH_CODES and not self._destination_online():
            # The path went missing because the volume did: catching it here stops the
            # job at the first failed file instead of waiting for the watchdog.
            self._abort_fatal(
                f"Hedef disk sistemden koptu: {self._dest_root or self.destination} "
                f"artik erisilemiyor ({win_error_text(code)}). "
                f"O ana kadar {format_bytes(self._current_bytes)} aktarilmisti."
            )

    def _heartbeat_loop(self):
        """Background ticker that keeps the Mini Floating Bar and UI updated every 150ms non-stop."""
        while self.is_running and not self.cancel_event.is_set():
            time.sleep(0.15)
            if self.is_running and self._global_total_bytes > 0:
                self._report(self._current_bytes, self._global_total_bytes, self._current_label, force=True)

    def run(self):
        """Main execution method, run inside a background thread."""
        self.is_running = True
        self.failed_items.clear()
        start_time = time.time()

        # Allocate 64KB-aligned 4MB DMA buffer via VirtualAlloc
        self._dma_buf = kernel32.VirtualAlloc(
            None, self.DMA_CHUNK_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )

        try:
            if not self.sources:
                raise FileNotFoundError("Kopyalanacak hicbir kaynak dosya veya klasor secilmedi.")

            self._dest_root = os.path.splitdrive(os.path.abspath(self.destination))[0] or self.destination
            if not self._dest_root.endswith(os.sep):
                self._dest_root += os.sep
            if not self._destination_online():
                raise OSError(
                    f"Hedef disk erisilemiyor: {self._dest_root} "
                    f"Surucunun bagli oldugunu kontrol edip tekrar deneyin."
                )

            os.makedirs(self.destination, exist_ok=True)

            # Match the worker count to the target medium: rotating disks lose throughput
            # when many threads write at once, SSDs need the parallelism to stay saturated.
            self.disk_profile = detect_disk_profile(self.destination)
            self.effective_threads = recommended_threads(self.disk_profile, self.threads)
            self.log(
                f"[DISK] Hedef: {PROFILE_LABELS.get(self.disk_profile, self.disk_profile)} "
                f"-> {self.effective_threads} is parcacigi (istenen: {self.threads})",
                "info",
            )

            self.log(f"[BASLAT] Kopyalama baslatiliyor -> {self.destination}", "info")
            if self.mode == "turbo_kernel":
                mode_desc = "TURBO NON-STOP DMA"
            elif self.mode == "paket":
                mode_desc = "PAKET (TEK DOSYA AKISI)"
            else:
                mode_desc = self.mode.upper()
            if self.sync_mode:
                mode_desc += " + SENKRONIZASYON (Sadece Farklar)"
            if self.speed_limit_mbps:
                mode_desc += f" (Hiz Limiti: {self.speed_limit_mbps} MB/s)"
            self.log(f"[MOTOR] Mod: {mode_desc}", "info")

            # Build items list, reusing precomputed sizes from UI queue when available for instant start
            pre_map = {os.path.abspath(it["path"]): it["bytes"] for it in self.precomputed_items if "path" in it and "bytes" in it}
            global_total_bytes = 0
            items_info = []

            for s in self.sources:
                if s in pre_map and pre_map[s] > 0:
                    s_bytes = pre_map[s]
                else:
                    s_bytes, _ = get_path_size(s)
                global_total_bytes += s_bytes
                items_info.append({
                    "path": s,
                    "is_dir": os.path.isdir(s),
                    "bytes": s_bytes,
                })

            self._global_total_bytes = global_total_bytes
            self._current_bytes = 0
            self.log(f"[BOYUT] Toplam: {format_bytes(global_total_bytes)} ({len(self.sources)} ana oge)", "info")

            # Reset transfer timers right before actual I/O starts
            start_time = time.time()
            self._total_paused_time = 0.0
            self._unflushed_small_bytes = 0
            self._throttle_start = start_time
            self._throttle_bytes = 0
            self._prog_last_time = start_time
            self._prog_last_bytes = 0
            self._speed_hist = []
            self._last_speed = 0.0

            if global_total_bytes == 0:
                self._emit_finished(True, "Secilen ogeler bos veya 0 bayt.", 0, start_time)
                return

            # Start 150ms non-stop UI heartbeat ticker
            hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            hb_thread.start()

            # Watches for the destination vanishing underneath us.
            wd_thread = threading.Thread(target=self._device_watchdog_loop, daemon=True)
            wd_thread.start()

            if self.mode == "paket":
                self._run_package_mode(items_info, global_total_bytes, start_time)
                return

            accumulated_bytes = 0
            total_items_count = len(items_info)

            # Copy each source item sequentially without upfront folder-walk stalls
            for idx, item in enumerate(items_info):
                if self.cancel_event.is_set():
                    break

                self._wait_if_paused()

                src_path = item["path"]
                base_name = os.path.basename(src_path.rstrip("/\\"))
                is_dir = item["is_dir"]
                item_bytes = item["bytes"]
                current_label = f"[{idx + 1}/{total_items_count}] {base_name}"
                self._current_label = current_label

                self.log(f"[OGE] Aktariliyor: {current_label} ({format_bytes(item_bytes)})", "info")
                self._report(accumulated_bytes, global_total_bytes, current_label, force=True)

                try:
                    if is_dir:
                        target_folder = os.path.join(self.destination, base_name)
                        os.makedirs(target_folder, exist_ok=True)
                        if self.mode == "robocopy":
                            success = self._copy_folder_robocopy(
                                src_path, target_folder, item_bytes, accumulated_bytes, global_total_bytes, current_label
                            )
                        else:
                            success = self._copy_folder_kernel(
                                src_path, target_folder, item_bytes, accumulated_bytes, global_total_bytes, current_label
                            )
                    else:
                        target_file = os.path.join(self.destination, base_name)
                        if self.sync_mode and is_file_identical(src_path, target_file):
                            self.log(f"[SENKRON ATLANDI] Dosya hedefte ayni ve guncel: {base_name}", "info")
                            accumulated_bytes += item_bytes
                            self._current_bytes = accumulated_bytes
                            self._report(accumulated_bytes, global_total_bytes, current_label, force=True)
                            continue

                        success = self._dma_copy_file(
                            src_path, target_file, accumulated_bytes, global_total_bytes, current_label, item_bytes
                        )

                    if not success and not self.cancel_event.is_set() and not self.fatal_error:
                        self.failed_items.append({"source": src_path, "destination": self.destination, "error": "Kopyalama basarisiz oldu"})
                        self.log(f"[UYARI] {base_name} kopyalanamadi, atlandi.", "warning")

                except Exception as ex:
                    self._note_os_error(ex, self.destination, writing=True)
                    self.failed_items.append({"source": src_path, "destination": self.destination, "error": str(ex)})
                    self.log(f"[HATA] {base_name} atlandi: {str(ex)}", "error")

                accumulated_bytes += item_bytes
                self._current_bytes = accumulated_bytes
                self._report(accumulated_bytes, global_total_bytes, current_label, force=True)

            if self.cancel_event.is_set():
                reason = self.fatal_error or "Islem kullanici tarafindan iptal edildi."
                self._emit_finished(False, reason, self._current_bytes, start_time)
                return

            # Optional MD5 verification
            if self.verify and not self.cancel_event.is_set():
                self.log("[DOGRULAMA] Dogrulama baslatildi: MD5 Checksum kontrol ediliyor...", "info")
                all_ok = True
                for item in items_info:
                    if self.cancel_event.is_set():
                        return
                    src_path = item["path"]
                    base_name = os.path.basename(src_path.rstrip("/\\"))
                    target_path = os.path.join(self.destination, base_name)
                    if not self._verify_copy(src_path, target_path, item["is_dir"]):
                        all_ok = False
                        break
                if not all_ok:
                    self._emit_finished(False, "Dogrulama basarisiz! Bazi dosyalar hedefte eslesmedi.", global_total_bytes, start_time)
                    return
                self.log("[BASARILI] Dogrulama basarili: Tum dosyalar %100 birebir ayni ve saglam!", "success")

            duration = max(0.001, time.time() - start_time)
            avg_speed = global_total_bytes / duration
            self.log(f"[TAMAMLANDI] Kopyalama tamamlandi! Sure: {format_time(duration)}, Ortalama Hiz: {format_bytes(avg_speed)}/s", "success")

            finish_msg = "Kopyalama basariyla tamamlandi!"
            if self.failed_items:
                finish_msg = f"Kopyalama tamamlandi, ancak {len(self.failed_items)} oge atlandi/hatali."

            self._emit_finished(True, finish_msg, global_total_bytes, start_time)

        except Exception as e:
            self.log(f"[HATA] Kritik Hata: {str(e)}", "error")
            self._emit_finished(False, str(e), 0, start_time)
        finally:
            self.is_running = False
            if self._dma_buf:
                try:
                    kernel32.VirtualFree(self._dma_buf, 0, MEM_RELEASE)
                except Exception:
                    pass
                self._dma_buf = None

    def _wait_if_paused(self):
        if self.is_paused:
            self._report(self._current_bytes, self._global_total_bytes, self._current_label, force=True)
            pause_start = time.time()
            while self.is_paused and not self.cancel_event.is_set():
                time.sleep(0.05)
            paused_delta = time.time() - pause_start
            self._total_paused_time += paused_delta
            self._throttle_start += paused_delta
            self._prog_last_time += paused_delta
            self._report(self._current_bytes, self._global_total_bytes, self._current_label, force=True)

    def _throttle(self, chunk_len: int):
        if not self.speed_limit_mbps:
            return
        self._throttle_bytes += chunk_len
        limit = self.speed_limit_mbps * 1024 * 1024
        expected = self._throttle_bytes / limit
        elapsed = time.time() - self._throttle_start
        if expected > elapsed:
            time.sleep(expected - elapsed)

    def _report(self, current_overall: int, global_total: int, label: str, force: bool = False):
        with self._report_lock:
            now = time.time()
            elapsed = now - self._prog_last_time
            if not force and elapsed < 0.12:
                return
            if not self.is_paused and elapsed >= 0.10:
                delta_b = max(0, current_overall - self._prog_last_bytes)
                inst = delta_b / elapsed
                if delta_b > 0 or elapsed >= 0.45:
                    self._speed_hist.append(inst)
                    if len(self._speed_hist) > 10:
                        self._speed_hist.pop(0)
                    self._last_speed = sum(self._speed_hist) / len(self._speed_hist)
                self._prog_last_time = now
                self._prog_last_bytes = current_overall

            speed = 0.0 if self.is_paused else self._last_speed
            eta = max(0, global_total - current_overall) / speed if speed > 1024 else 0
            if self.on_progress:
                self.on_progress({
                    "bytes_transferred": min(current_overall, global_total),
                    "total_bytes": global_total,
                    "percent": min(100.0, (current_overall / global_total * 100)) if global_total > 0 else 0,
                    "speed": speed,
                    "eta": eta,
                    "current_file": label,
                    "is_paused": self.is_paused,
                })

    def _dma_copy_file(self, src: str, dst: str, base_offset: int, global_total: int, label: str, fsize: int = -1) -> bool:
        """
        Copy a single file using Win32 CreateFileW:
        - Source opened with FILE_FLAG_SEQUENTIAL_SCAN (kernel read-ahead prefetch)
        - Destination opened with FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN for files >= 64KB
          so writes go directly to disk via hardware DMA without filling Windows RAM dirty-page cache.
        """
        self._wait_if_paused()
        if self.cancel_event.is_set():
            return False

        if not self.overwrite and os.path.exists(dst):
            self._current_bytes = base_offset + max(0, fsize)
            return True

        if fsize < 0:
            try:
                fsize = os.path.getsize(src)
            except OSError:
                fsize = 0

        self._current_label = label

        # Open source handle with sequential read-ahead
        h_in = kernel32.CreateFileW(
            src,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_SEQUENTIAL_SCAN,
            None,
        )
        if h_in == INVALID_HANDLE_VALUE or h_in is None:
            code = ctypes.get_last_error()
            self._note_win_error(code, src, writing=False)
            raise OSError(f"Kaynak dosya acilamadi: {src} -> {win_error_text(code)}")

        use_no_buffering = fsize >= (2 * 1024 * 1024) and self._dma_buf is not None
        if not use_no_buffering:
            self._unflushed_small_bytes += fsize
            if self._unflushed_small_bytes >= 64 * 1024 * 1024:
                self._unflushed_small_bytes = 0
                out_flags = FILE_FLAG_SEQUENTIAL_SCAN | FILE_FLAG_WRITE_THROUGH
            else:
                out_flags = FILE_FLAG_SEQUENTIAL_SCAN
        else:
            out_flags = FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN

        h_out = kernel32.CreateFileW(
            dst,
            GENERIC_WRITE,
            0,
            None,
            CREATE_ALWAYS,
            out_flags,
            None,
        )
        if (h_out == INVALID_HANDLE_VALUE or h_out is None) and use_no_buffering:
            # Automatic fallback if target filesystem does not support sector-aligned NO_BUFFERING
            use_no_buffering = False
            h_out = kernel32.CreateFileW(
                dst,
                GENERIC_WRITE,
                0,
                None,
                CREATE_ALWAYS,
                FILE_FLAG_SEQUENTIAL_SCAN | FILE_FLAG_WRITE_THROUGH,
                None,
            )

        if h_out == INVALID_HANDLE_VALUE or h_out is None:
            code = ctypes.get_last_error()
            kernel32.CloseHandle(h_in)
            self._note_win_error(code, dst, writing=True)
            raise OSError(f"Hedef dosya olusturulamadi: {dst} -> {win_error_text(code)}")

        rd = wintypes.DWORD(0)
        wr = wintypes.DWORD(0)
        transferred = 0
        copy_ok = True

        try:
            while True:
                self._wait_if_paused()
                if self.cancel_event.is_set():
                    copy_ok = False
                    break

                ok_r = kernel32.ReadFile(h_in, self._dma_buf, self.DMA_CHUNK_SIZE, ctypes.byref(rd), None)
                if not ok_r:
                    code = ctypes.get_last_error()
                    self._note_win_error(code, src, writing=False)
                    raise OSError(f"Okuma hatasi: {src} -> {win_error_text(code)}")
                n_read = rd.value
                if n_read == 0:
                    break

                write_len = ((n_read + 4095) & ~4095) if use_no_buffering else n_read
                ok_w = kernel32.WriteFile(h_out, self._dma_buf, write_len, ctypes.byref(wr), None)
                if not ok_w:
                    code = ctypes.get_last_error()
                    self._note_win_error(code, dst, writing=True)
                    raise OSError(f"Yazma hatasi: {dst} -> {win_error_text(code)}")

                transferred += n_read
                self._current_bytes = base_offset + transferred
                self._throttle(n_read)
                self._report(self._current_bytes, global_total, label)

            if copy_ok:
                # Truncate sector-padded tail to exact byte size directly on the open handle
                if use_no_buffering and (transferred & 4095) != 0:
                    eof_info = FILE_END_OF_FILE_INFO(transferred)
                    kernel32.SetFileInformationByHandle(
                        h_out, FileEndOfFileInfo, ctypes.byref(eof_info), ctypes.sizeof(eof_info)
                    )

                # Copy CreationTime, LastAccessTime, LastWriteTime directly via open Win32 handles (0 extra syscalls)
                ctime, atime, mtime = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
                if kernel32.GetFileTime(h_in, ctypes.byref(ctime), ctypes.byref(atime), ctypes.byref(mtime)):
                    kernel32.SetFileTime(h_out, ctypes.byref(ctime), ctypes.byref(atime), ctypes.byref(mtime))
        finally:
            kernel32.CloseHandle(h_in)
            kernel32.CloseHandle(h_out)

        if not copy_ok or self.cancel_event.is_set():
            try:
                os.remove(dst)
            except OSError:
                pass
            return False

        return True

    def _fast_small_copy(self, src: str, dst: str, fsize: int) -> None:
        """
        Buffered copy for small files. Thread-safe: never touches the shared DMA buffer.

        What costs the time on small files is metadata, not data: on a rotating disk
        every extra open of the destination is another head movement. The previous
        version opened the destination once to write it and then let shutil.copystat
        reopen it again just to carry the timestamps across, which measured as about a
        third of the entire per-file cost. So everything now happens through one handle
        that stays open: the final size is declared upfront instead of letting NTFS
        extend the allocation write by write, and the timestamps are stamped before the
        handle closes. Attributes are only written when the source actually carries one,
        because that call costs another open of the slow disk.
        """
        h_in = kernel32.CreateFileW(
            src, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, None,
        )
        if h_in == INVALID_HANDLE_VALUE or h_in is None:
            code = ctypes.get_last_error()
            self._note_win_error(code, src, writing=False)
            raise OSError(f"Kaynak dosya acilamadi: {src} -> {win_error_text(code)}")

        h_out = None
        try:
            h_out = kernel32.CreateFileW(
                dst, GENERIC_WRITE, 0, None, CREATE_ALWAYS, FILE_FLAG_SEQUENTIAL_SCAN, None,
            )
            if h_out == INVALID_HANDLE_VALUE or h_out is None:
                code = ctypes.get_last_error()
                h_out = None
                self._note_win_error(code, dst, writing=True)
                raise OSError(f"Hedef dosya olusturulamadi: {dst} -> {win_error_text(code)}")

            if fsize > 0:
                eof_info = FILE_END_OF_FILE_INFO(fsize)
                kernel32.SetFileInformationByHandle(
                    h_out, FileEndOfFileInfo, ctypes.byref(eof_info), ctypes.sizeof(eof_info)
                )

            chunk_size = min(max(fsize, 64 * 1024), 1024 * 1024)
            buf = ctypes.create_string_buffer(chunk_size)
            rd = wintypes.DWORD(0)
            wr = wintypes.DWORD(0)
            written = 0

            while True:
                if not kernel32.ReadFile(h_in, buf, chunk_size, ctypes.byref(rd), None):
                    code = ctypes.get_last_error()
                    self._note_win_error(code, src, writing=False)
                    raise OSError(f"Okuma hatasi: {src} -> {win_error_text(code)}")
                if rd.value == 0:
                    break
                if not kernel32.WriteFile(h_out, buf, rd.value, ctypes.byref(wr), None):
                    code = ctypes.get_last_error()
                    self._note_win_error(code, dst, writing=True)
                    raise OSError(f"Yazma hatasi: {dst} -> {win_error_text(code)}")
                written += wr.value

            if written != fsize:
                # The source was not the size we were told: trim the declared size back.
                eof_info = FILE_END_OF_FILE_INFO(written)
                kernel32.SetFileInformationByHandle(
                    h_out, FileEndOfFileInfo, ctypes.byref(eof_info), ctypes.sizeof(eof_info)
                )

            ctime, atime, mtime = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
            if kernel32.GetFileTime(h_in, ctypes.byref(ctime), ctypes.byref(atime), ctypes.byref(mtime)):
                kernel32.SetFileTime(h_out, ctypes.byref(ctime), ctypes.byref(atime), ctypes.byref(mtime))
        finally:
            kernel32.CloseHandle(h_in)
            if h_out:
                kernel32.CloseHandle(h_out)

        src_attrs = kernel32.GetFileAttributesW(src)
        if src_attrs != 0xFFFFFFFF and (src_attrs & COPYABLE_ATTRIBUTES):
            kernel32.SetFileAttributesW(dst, src_attrs & COPYABLE_ATTRIBUTES)

    def _copy_small_batch_parallel(self, batch, done_in_folder: int, base_offset: int,
                                   global_total: int, current_label: str) -> int:
        """Copies a batch of small files with a worker pool and reports aggregated progress."""
        workers = max(1, min(self.effective_threads, 32, len(batch)))
        progress_lock = threading.Lock()
        state = {"done": done_in_folder}

        def _one(job):
            s_file, d_file, fsize, fname = job
            if self.cancel_event.is_set():
                return
            self._wait_if_paused()

            if not (self.sync_mode and is_file_identical(s_file, d_file)):
                try:
                    self._fast_small_copy(s_file, d_file, fsize)
                except Exception as e:
                    self._note_os_error(e, d_file, writing=True)
                    self.failed_items.append({"source": s_file, "destination": d_file, "error": str(e)})
                    self.log(f"[HATA] {fname} atlandi: {e}", "error")
                    return
                self._throttle(fsize)

            with progress_lock:
                state["done"] += fsize
                current = base_offset + state["done"]
            self._current_bytes = current
            self._current_label = f"{current_label} / {fname}"
            self._report(current, global_total, self._current_label)

        if workers <= 1:
            for job in batch:
                if self.cancel_event.is_set():
                    break
                _one(job)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_one, batch))

        return state["done"]

    def _copy_folder_kernel(self, src_dir: str, dst_dir: str, dir_size: int, base_offset: int, global_total: int, current_label: str) -> bool:
        """
        Stream directory traversal and file copying on the fly without any upfront os.walk freeze
        or multi-threaded mechanical HDD head thrashing.
        """
        done_in_folder = 0
        stack = [(src_dir, dst_dir)]

        while stack:
            if self.cancel_event.is_set():
                return False
            self._wait_if_paused()

            cur_src, cur_dst = stack.pop()
            try:
                os.makedirs(cur_dst, exist_ok=True)
            except OSError as e:
                self._note_os_error(e, cur_dst, writing=True)
                self.failed_items.append({"source": cur_src, "destination": cur_dst, "error": str(e)})
                continue

            subdirs = []
            files = []
            try:
                with os.scandir(cur_src) as it:
                    for entry in it:
                        if self.cancel_event.is_set():
                            return False
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append((entry.path, os.path.join(cur_dst, entry.name)))
                            elif entry.is_file(follow_symlinks=False):
                                fsize = entry.stat(follow_symlinks=False).st_size
                                files.append((entry.path, os.path.join(cur_dst, entry.name), fsize, entry.name))
                        except (OSError, PermissionError) as e:
                            self.failed_items.append({"source": entry.path, "destination": cur_dst, "error": str(e)})
            except (OSError, PermissionError) as e:
                self.failed_items.append({"source": cur_src, "destination": cur_dst, "error": str(e)})
                continue

            # Push subdirectories in reverse order so traversal order is natural
            for sd in reversed(subdirs):
                stack.append(sd)

            # Split the batch: small files go through a parallel pool (per-file overhead dominates
            # there, so one stream leaves the disk idle), large files keep the single DMA stream.
            small = [f for f in files if f[2] < self.SMALL_FILE_LIMIT]
            large = [f for f in files if f[2] >= self.SMALL_FILE_LIMIT]

            if small:
                done_in_folder = self._copy_small_batch_parallel(
                    small, done_in_folder, base_offset, global_total, current_label
                )
                if self.cancel_event.is_set():
                    return False

            # Copy large files sequentially with hardware DMA for zero HDD head thrashing
            for s_file, d_file, fsize, fname in large:
                if self.cancel_event.is_set():
                    return False
                self._wait_if_paused()

                file_label = f"{current_label} / {fname}"
                self._current_label = file_label

                if self.sync_mode and is_file_identical(s_file, d_file):
                    done_in_folder += fsize
                    self._current_bytes = base_offset + done_in_folder
                    self._report(self._current_bytes, global_total, file_label)
                    continue

                try:
                    self._dma_copy_file(
                        s_file, d_file, base_offset + done_in_folder, global_total, file_label, fsize
                    )
                except Exception as e:
                    self._note_os_error(e, d_file, writing=True)
                    self.failed_items.append({"source": s_file, "destination": d_file, "error": str(e)})
                    self.log(f"[HATA] {fname} atlandi: {e}", "error")

                if self.cancel_event.is_set():
                    return False

                done_in_folder += fsize
                self._current_bytes = base_offset + done_in_folder
                self._report(self._current_bytes, global_total, file_label)

        return True

    def _sample_avg_file_size(self, root: str, sample_limit: int = 400) -> float:
        """Quickly samples up to N files to decide whether unbuffered I/O (/J) is worth using."""
        total = 0
        count = 0
        stack = [root]
        while stack and count < sample_limit:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        if count >= sample_limit:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                                count += 1
                        except OSError:
                            continue
            except OSError:
                continue
        return (total / count) if count else 0.0

    def _copy_folder_robocopy(self, src_dir: str, dst_dir: str, dir_size: int, base_offset: int, global_total: int, current_label: str) -> bool:
        """
        Copies a folder with Robocopy at full throttle.

        Robocopy's per-file percentage output (`/NP` off) writes thousands of CR-terminated
        lines per second into a 4 KB OS pipe. If the reader cannot drain it fast enough,
        robocopy blocks on WriteFile(stdout) and the copy collapses to a few KB/s.
        So progress output is disabled entirely and byte accounting is done from the
        one-line-per-file summary instead, which is drained by a dedicated reader thread
        so the copy never waits on the UI.
        """
        threads = min(max(1, self.effective_threads), 128)
        avg_size = self._sample_avg_file_size(src_dir)
        # /J (unbuffered) only pays off on large files; on small files it destroys throughput.
        use_unbuffered = avg_size >= (8 * 1024 * 1024)
        # Robocopy prints a file's percentage while the copy is in flight only when it
        # runs single-streamed; under /MT it stays silent until the file is finished, so
        # a multi-gigabyte file freezes the display for as long as it takes. Where the
        # medium already limits us to one worker, dropping /MT costs no throughput and
        # buys live progress; with many workers the percentage output would flood the
        # pipe, which is what collapsed throughput to a few KB/s in the first place.
        live_progress = use_unbuffered and threads == 1

        cmd = [
            "robocopy",
            src_dir,
            dst_dir,
            "/E",
            "/R:0",
            "/W:0",
            "/BYTES",
            "/FFT",
            "/XJ",
            "/NDL",
            "/NJH",
            "/NJS",
        ]
        if use_unbuffered:
            cmd.append("/J")
        if not live_progress:
            # /MT suppresses the percentage output even at /MT:1, so it has to be absent.
            cmd.extend([f"/MT:{threads}", "/NP"])

        if self.sync_mode:
            cmd.extend(["/XO", "/XC", "/XN"])
        elif not self.overwrite:
            cmd.extend(["/XC", "/XN", "/XO"])

        self.log(
            f"[ROBOCOPY] {'tek akis + canli ilerleme' if live_progress else f'MT:{threads}'} "
            f"| {'unbuffered /J' if use_unbuffered else 'onbellekli (kucuk dosya modu)'} "
            f"| ort. dosya {format_bytes(avg_size)}: {current_label}",
            "info",
        )
        self._current_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc = self._current_proc

        line_q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _drain():
            """
            Dedicated reader: keeps robocopy's stdout pipe empty at all times.

            Percentage updates are terminated with CR, not LF, because robocopy redraws
            them over the same line, so readline() would sit on one until the next file
            finished. Reading whatever is available and splitting on both terminators
            keeps the pipe drained and the percentages flowing.
            """
            buf = b""
            try:
                while True:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    buf = (buf + chunk).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                    pieces = buf.split(b"\n")
                    buf = pieces.pop()
                    for raw in pieces:
                        line_q.put(raw.decode("utf-8", errors="replace"))
                if buf:
                    line_q.put(buf.decode("utf-8", errors="replace"))
            except Exception:
                pass
            finally:
                line_q.put(None)

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()

        done_bytes = 0
        # Bytes of the file currently in flight: counted separately because its size is
        # only known in full once the next file record arrives.
        pending_size = 0
        pending_done = 0
        reader_finished = False

        while True:
            if self.cancel_event.is_set():
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return False

            self._wait_if_paused()

            try:
                line = line_q.get(timeout=0.15)
            except queue.Empty:
                # No output yet (robocopy is busy on a large file) - keep the UI alive.
                self._report(
                    base_offset + min(dir_size, done_bytes + pending_done),
                    global_total,
                    self._current_label,
                )
                if reader_finished and proc.poll() is not None:
                    break
                continue

            if line is None:
                reader_finished = True
                if proc.poll() is not None and line_q.empty():
                    break
                continue

            # With /BYTES + /NP a copied file prints as:
            #   [TAB] New File [TAB][TAB] 7 [TAB] C:/yol/dosya.txt
            # so the byte count is the second-to-last tab field, the path is the last.
            if "\t" in line:
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) >= 2:
                    size_tok = parts[-2].strip()
                    if size_tok.isdigit():
                        if live_progress:
                            # Single-streamed, the record is printed as the file starts,
                            # so this record means the previous one is finished.
                            done_bytes += pending_size
                            pending_size = int(size_tok)
                            pending_done = 0
                        else:
                            done_bytes += int(size_tok)
                        name = os.path.basename(parts[-1].strip().rstrip("\\/"))
                        if name:
                            self._current_label = f"{current_label} / {name}"
            elif live_progress:
                token = line.strip()
                if token.endswith("%"):
                    try:
                        pct = float(token[:-1])
                    except ValueError:
                        pct = -1.0
                    if 0.0 <= pct <= 100.0:
                        pending_done = int(pending_size * pct / 100.0)

            self._current_bytes = base_offset + min(dir_size, done_bytes + pending_done)
            self._report(self._current_bytes, global_total, self._current_label)

        # The last file never gets a following record to close it out.
        done_bytes += pending_size
        pending_done = 0
        self._current_bytes = base_offset + min(dir_size, done_bytes)
        self._report(self._current_bytes, global_total, self._current_label, force=True)

        try:
            proc.stdout.close()
        except Exception:
            pass
        exit_code = proc.poll()
        self._current_proc = None
        return exit_code is not None and exit_code < 8

    # ------------------------------------------------------------------
    # Package mode
    # ------------------------------------------------------------------
    # Thousands of small files are slow for one reason, and it is not the bus: every
    # file costs NTFS a metadata round trip, which on a rotating USB disk means moving
    # the head. Measured on this machine, 20480 files of 6 KB reach 2-2.7 MB/s while a
    # single sequential stream to the same disk reaches 110 MB/s. Package mode writes
    # exactly that: the whole tree becomes one .tar file, so the disk does one long
    # sequential write instead of twenty thousand scattered small ones. The price is
    # that the files stay inside a container until the package is opened again, which
    # this same mode does when it is pointed at a .tar.

    def _run_package_mode(self, items_info, global_total, start_time):
        """Entry point for 'paket': unpacks a single .tar source, otherwise packs."""
        if (len(items_info) == 1 and not items_info[0]["is_dir"]
                and items_info[0]["path"].lower().endswith(".tar")):
            self._run_package_unpack(items_info[0]["path"], items_info[0]["bytes"], start_time)
        else:
            self._run_package_pack(items_info, global_total, start_time)

    def _package_entries(self, items_info):
        """
        Flat list of everything that goes into the archive: (source, name, is_dir, size).
        Walking upfront is what makes the progress bar and the file labels honest, and
        it only reads directory metadata - cheap next to writing the data.
        """
        entries = []
        for item in items_info:
            src = item["path"]
            if not item["is_dir"]:
                entries.append((src, os.path.basename(src), False, item["bytes"]))
                continue

            root_name = os.path.basename(src.rstrip("/\\")) or "kaynak"
            entries.append((src, root_name, True, 0))
            for cur, dirs, files in os.walk(src):
                if self.cancel_event.is_set():
                    return entries
                rel = os.path.relpath(cur, src)
                base = root_name if rel == "." else os.path.join(root_name, rel)
                for d in dirs:
                    entries.append((os.path.join(cur, d), os.path.join(base, d), True, 0))
                for f in files:
                    full = os.path.join(cur, f)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    entries.append((full, os.path.join(base, f), False, size))
        return entries

    def _package_archive_path(self):
        """Destination archive name: the source's own name plus a timestamp."""
        if len(self.sources) == 1:
            base = os.path.basename(self.sources[0].rstrip("/\\")) or "paket"
            if os.path.isfile(self.sources[0]):
                base = os.path.splitext(base)[0]
        else:
            base = "paket"
        safe = "".join(c for c in base if c not in '<>:"/\\|?*').strip() or "paket"
        return os.path.join(self.destination, f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}.tar")

    def _run_package_pack(self, items_info, global_total, start_time):
        self._current_label = "Paket icerigi taraniyor..."
        self._report(0, global_total, self._current_label, force=True)
        entries = self._package_entries(items_info)
        file_count = sum(1 for e in entries if not e[2])

        if self.cancel_event.is_set():
            self._emit_finished(False, self.fatal_error or "Islem kullanici tarafindan iptal edildi.",
                                self._current_bytes, start_time)
            return

        archive = self._package_archive_path()
        self.archive_path = archive
        if os.path.exists(archive) and not self.overwrite:
            self._emit_finished(False, f"Paket zaten var ve uzerine yazma kapali: {archive}", 0, start_time)
            return

        self.log(f"[PAKET] {file_count} dosya tek pakete yaziliyor: {archive}", "info")
        self.log("[PAKET] Hedef diske tek uzun akis yazilir; dosyalar paketin icinde kalir.", "info")

        # Scanning is over; the speed shown should describe the writing only.
        start_time = time.time()
        self._total_paused_time = 0.0
        self._throttle_start = start_time
        self._throttle_bytes = 0
        self._prog_last_time = start_time
        self._prog_last_bytes = 0
        self._speed_hist = []
        self._last_speed = 0.0

        done = 0
        cancelled = False
        try:
            with open(archive, "wb", buffering=self.PACK_CHUNK) as raw:
                with tarfile.open(fileobj=raw, mode="w|", bufsize=self.PACK_CHUNK,
                                  format=tarfile.PAX_FORMAT) as tf:
                    for path, name, is_dir, size in entries:
                        if self.cancel_event.is_set():
                            cancelled = True
                            break
                        self._wait_if_paused()
                        arcname = name.replace("\\", "/")

                        try:
                            info = tf.gettarinfo(path, arcname=arcname)
                        except OSError as exc:
                            self._note_os_error(exc, path, writing=False)
                            self.failed_items.append({"source": path, "destination": archive,
                                                      "error": f"Bilgisi alinamadi: {exc}"})
                            continue

                        if is_dir:
                            tf.addfile(info)
                            continue

                        self._current_label = os.path.basename(path)
                        info.size = size
                        try:
                            with open(path, "rb", buffering=0) as fh:
                                tf.addfile(info, PackageReader(self, fh, size, done, global_total, path))
                        except PackageCancelled:
                            cancelled = True
                            break
                        except OSError as exc:
                            # Opening failed, so nothing was streamed: the archive stays
                            # consistent and only this one file is lost.
                            self._note_os_error(exc, path, writing=False)
                            self.failed_items.append({"source": path, "destination": archive,
                                                      "error": f"Acilamadi: {exc}"})

                        done += size
                        self._current_bytes = done
                        self._report(done, global_total, self._current_label)
        except PackageCancelled:
            cancelled = True
        except OSError as exc:
            self._note_os_error(exc, archive, writing=True)
            self._emit_finished(False, self.fatal_error or f"Paket yazilamadi: {exc}",
                                self._current_bytes, start_time)
            return

        if cancelled or self.cancel_event.is_set():
            reason = self.fatal_error or "Islem kullanici tarafindan iptal edildi."
            # A half-written archive cannot be opened, so it is not left behind.
            try:
                os.remove(archive)
                self.log("[PAKET] Yarim kalan paket silindi.", "warning")
            except OSError:
                pass
            self._emit_finished(False, reason, self._current_bytes, start_time)
            return

        self._current_bytes = done
        self._report(done, global_total, self._current_label, force=True)

        if self.verify:
            self.log("[DOGRULAMA] Paket icerigi kaynakla karsilastiriliyor (MD5)...", "info")
            if not self._verify_package(archive, entries):
                self._emit_finished(False, "Dogrulama basarisiz! Paket icerigi kaynakla eslesmedi.",
                                    done, start_time)
                return
            self.log("[BASARILI] Paket dogrulandi: tum dosyalar birebir ayni.", "success")

        duration = max(0.001, time.time() - start_time)
        try:
            arc_size = os.path.getsize(archive)
        except OSError:
            arc_size = done
        self.log(f"[TAMAMLANDI] Paket yazildi: {archive} ({format_bytes(arc_size)}), "
                 f"Sure: {format_time(duration)}, Ortalama Hiz: {format_bytes(done / duration)}/s", "success")
        msg = f"Paket olusturuldu: {os.path.basename(archive)}"
        if self.failed_items:
            msg += f" ({len(self.failed_items)} dosya atlandi/hatali)"
        self._emit_finished(True, msg, done, start_time)

    def _safe_member_path(self, name: str):
        """
        Where a member is allowed to land. A tar can name a path full of '..' or an
        absolute one, which would write outside the chosen folder; anything that does
        not stay inside the destination is refused instead of extracted.
        """
        dest = os.path.abspath(self.destination)
        target = os.path.abspath(os.path.join(dest, name.replace("/", os.sep)))
        if target == dest or target.startswith(dest + os.sep):
            return target
        return None

    def _run_package_unpack(self, archive: str, archive_bytes: int, start_time: float):
        self.archive_path = archive
        self.log(f"[PAKET AC] {archive} icerigi aciliyor -> {self.destination}", "info")

        start_time = time.time()
        self._throttle_start = start_time
        self._prog_last_time = start_time
        done = 0
        count = 0
        cancelled = False

        try:
            with open(archive, "rb", buffering=self.PACK_CHUNK) as raw:
                with tarfile.open(fileobj=raw, mode="r|") as tf:
                    for info in tf:
                        if self.cancel_event.is_set():
                            cancelled = True
                            break
                        self._wait_if_paused()

                        target = self._safe_member_path(info.name)
                        if target is None:
                            self.failed_items.append({"source": info.name, "destination": self.destination,
                                                      "error": "Paket disina cikan yol, atlandi"})
                            self.log(f"[UYARI] Guvensiz yol atlandi: {info.name}", "warning")
                            continue

                        if info.isdir():
                            os.makedirs(target, exist_ok=True)
                            continue
                        if not info.isreg():
                            self.log(f"[ATLANDI] Normal dosya degil: {info.name}", "warning")
                            continue
                        if os.path.exists(target) and not self.overwrite:
                            self.log(f"[ATLANDI] Hedefte var, uzerine yazma kapali: {info.name}", "warning")
                            done += info.size
                            continue

                        self._current_label = os.path.basename(info.name)
                        parent = os.path.dirname(target)
                        if parent:
                            os.makedirs(parent, exist_ok=True)
                        reader = tf.extractfile(info)
                        if reader is None:
                            continue
                        try:
                            with open(target, "wb", buffering=self.PACK_CHUNK) as out:
                                while True:
                                    if self.cancel_event.is_set():
                                        cancelled = True
                                        break
                                    chunk = reader.read(self.PACK_CHUNK)
                                    if not chunk:
                                        break
                                    out.write(chunk)
                                    done += len(chunk)
                                    self._current_bytes = min(done, archive_bytes)
                                    self._throttle(len(chunk))
                                    self._report(self._current_bytes, archive_bytes, self._current_label)
                        except OSError as exc:
                            self._note_os_error(exc, target, writing=True)
                            self.failed_items.append({"source": info.name, "destination": target,
                                                      "error": str(exc)})
                            if self.fatal_error:
                                cancelled = True
                                break
                            continue
                        if cancelled:
                            break
                        try:
                            os.utime(target, (info.mtime, info.mtime))
                        except OSError:
                            pass
                        count += 1
        except tarfile.TarError as exc:
            self._emit_finished(False, f"Paket okunamadi (bozuk olabilir): {exc}",
                                self._current_bytes, start_time)
            return
        except OSError as exc:
            self._note_os_error(exc, self.destination, writing=True)
            self._emit_finished(False, self.fatal_error or f"Paket acilamadi: {exc}",
                                self._current_bytes, start_time)
            return

        if cancelled or self.cancel_event.is_set():
            self._emit_finished(False, self.fatal_error or "Islem kullanici tarafindan iptal edildi.",
                                self._current_bytes, start_time)
            return

        self._report(archive_bytes, archive_bytes, self._current_label, force=True)
        duration = max(0.001, time.time() - start_time)
        self.log(f"[TAMAMLANDI] Paket acildi: {count} dosya, Sure: {format_time(duration)}, "
                 f"Ortalama Hiz: {format_bytes(done / duration)}/s", "success")
        msg = f"Paket acildi: {count} dosya"
        if self.failed_items:
            msg += f" ({len(self.failed_items)} oge atlandi)"
        self._emit_finished(True, msg, done, start_time)

    def _verify_package(self, archive: str, entries) -> bool:
        """
        Reads the finished archive back and compares every member with its source by
        MD5. Only runs when verification was asked for, because it reads the whole
        archive off the slow disk a second time.
        """
        expected = {name.replace("\\", "/"): path for path, name, is_dir, _ in entries if not is_dir}
        seen = 0
        try:
            with open(archive, "rb", buffering=self.PACK_CHUNK) as raw:
                with tarfile.open(fileobj=raw, mode="r|") as tf:
                    for info in tf:
                        if self.cancel_event.is_set():
                            return False
                        if not info.isreg():
                            continue
                        source = expected.get(info.name)
                        if source is None:
                            continue
                        reader = tf.extractfile(info)
                        if reader is None:
                            continue
                        in_pkg = hashlib.md5()
                        while True:
                            chunk = reader.read(self.PACK_CHUNK)
                            if not chunk:
                                break
                            in_pkg.update(chunk)
                        on_disk = hashlib.md5()
                        with open(source, "rb", buffering=0) as fh:
                            while True:
                                chunk = fh.read(self.PACK_CHUNK)
                                if not chunk:
                                    break
                                on_disk.update(chunk)
                        if in_pkg.hexdigest() != on_disk.hexdigest():
                            self.log(f"[DOGRULAMA HATASI] Pakette farkli: {info.name}", "error")
                            return False
                        seen += 1
                        self._current_label = f"Dogrulaniyor: {os.path.basename(info.name)}"
                        self._report(self._current_bytes, self._global_total_bytes, self._current_label)
        except (tarfile.TarError, OSError) as exc:
            self.log(f"[DOGRULAMA HATASI] Paket okunamadi: {exc}", "error")
            return False
        self.log(f"[DOGRULAMA] {seen} dosya karsilastirildi.", "info")
        return True

    def _verify_copy(self, src: str, dst: str, is_dir: bool) -> bool:
        """Verifies MD5 checksum between source and destination."""
        if not is_dir:
            src_hash = calculate_hash(src, self.cancel_event)
            dst_hash = calculate_hash(dst, self.cancel_event)
            if self.cancel_event.is_set():
                return False
            self.log(f"Kaynak MD5: {src_hash}", "info")
            self.log(f"Hedef  MD5: {dst_hash}", "info")
            return src_hash == dst_hash and src_hash is not None

        for root, dirs, files in os.walk(src):
            if self.cancel_event.is_set():
                return False
            rel = os.path.relpath(root, src)
            target_root = dst if rel == "." else os.path.join(dst, rel)
            for f in files:
                s_file = os.path.join(root, f)
                d_file = os.path.join(target_root, f)
                if not os.path.exists(d_file) or os.path.getsize(s_file) != os.path.getsize(d_file):
                    return False
                s_h = calculate_hash(s_file, self.cancel_event)
                d_h = calculate_hash(d_file, self.cancel_event)
                if s_h != d_h:
                    return False
        return True

    def _emit_finished(self, success: bool, message: str, total_bytes: int, start_time: float):
        duration = max(0.001, (time.time() - start_time) - self._total_paused_time)
        avg_speed = total_bytes / duration if success else 0
        if self.on_finished:
            self.on_finished(success, message, {
                "duration": duration,
                "total_bytes": total_bytes,
                "avg_speed": avg_speed,
                "failed_items": list(self.failed_items),
            })
