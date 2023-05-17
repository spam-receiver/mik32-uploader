import shlex
import argparse
import subprocess
import os
from enum import Enum
from typing import List, Dict, NamedTuple
from tclrpc import OpenOcdTclRpc
import mik32_eeprom
import mik32_spifi
import mik32_ram
from mik32_parsers import *


# class bcolors(Enum):
#     OK = '\033[92m'
#     WARNING = '\033[93m'
#     FAIL = '\033[91m'
#     ENDC = '\033[0m'
#     BOLD = '\033[1m'
#     UNDERLINE = '\033[4m'


DEFAULT_OPENOCD_EXEC_FILE_PATH = os.path.join("openocd", "bin", "openocd.exe")
DEFAULT_OPENOCD_SCRIPTS_PATH = os.path.join(
    "openocd", "share", "openocd", "scripts")

supported_formats = [".hex"]


def test_connection():
    output = ""
    with OpenOcdTclRpc() as openocd:
        output = openocd.run(f"capture \"reg\"")

    if output == "":
        raise Exception("ERROR: no regs found, check MCU connection")


class MemoryType(Enum):
    BOOT = 0
    EEPROM = 1
    RAM = 2
    SPIFI = 80
    UNKNOWN = -1


class MemorySection(NamedTuple):
    type: MemoryType
    offset: int
    length: int  # Memory section length in bytes


mik32v0_sections: List[MemorySection] = [
    MemorySection(MemoryType.BOOT, 0x0, 16 * 1024),
    MemorySection(MemoryType.EEPROM, 0x01000000, 8 * 1024),
    MemorySection(MemoryType.RAM, 0x02000000, 16 * 1024),
    MemorySection(MemoryType.SPIFI, 0x80000000, 8 * 1024 * 1024),
]


@dataclass
class Segment:
    offset: int
    memory: MemorySection | None
    data: List[int]


def belongs_memory_section(memory_section: MemorySection, offset: int) -> bool:
    if offset < memory_section.offset:
        return False
    if offset >= (memory_section.offset + memory_section.length):
        return False

    return True


def find_memory_section(offset: int) -> MemorySection | None:
    for section in mik32v0_sections:
        if belongs_memory_section(section, offset):
            return section

    return None


def read_file(filename: str) -> List[Segment]:
    segments: List[Segment] = []
    lines: List[str] = []

    file_name, file_extension = os.path.splitext(filename)
    if file_extension in supported_formats:
        with open(filename) as f:
            lines = f.readlines()
    elif file_extension == ".bin":
        with open(filename, "rb") as f:
            contents = list(f.read())
            segments.append(
                Segment(offset=0, memory=find_memory_section(0), data=contents))
    else:
        raise Exception("Unsupported file format: %s" % (file_extension))

    lba: int = 0        # Linear Base Address
    expect_address = 0  # Address of the next byte

    for i, line in enumerate(lines):
        record: Record = parse_line(line, i, file_extension)
        if record.type == RecordType.DATA:
            drlo: int = record.address  # Data Record Load Offset
            if (expect_address != lba+drlo) or (segments.__len__() == 0):
                expect_address = lba+drlo
                segments.append(Segment(
                    offset=expect_address, memory=find_memory_section(expect_address), data=[]))

            for byte in record.data:
                segments[-1].data.append(byte)
                expect_address += 1
        elif record.type == RecordType.EXTADDR:
            lba = record.address
        elif record.type == RecordType.LINEARSTARTADDR:
            print("Start Linear Address:", record.address)
        elif record.type == RecordType.EOF:
            break

    return segments


def segments_to_pages(segments: List[Segment], page_size: int) -> List[Page]:
    pages: Dict[int, List[int]] = {}

    for segment in segments:
        if segment.memory is None:
            continue
    
        internal_offset = segment.offset - segment.memory.offset

        for i, byte in enumerate(segment.data):
            byte_offset = internal_offset + i

            pages[byte_offset % 256]
    
    return pages


def upload_file(filename: str, host: str = '127.0.0.1', port: int = OpenOcdTclRpc.DEFAULT_PORT, is_resume=True, run_openocd=False) -> int:
    """
    Write ihex or binary file into MIK32 EEPROM or external flash memory

    @filename: full path to the file with hex or bin file format

    @return: return 0 if successful, 1 if failed
    """

    # print("Running OpenOCD...")

    # print(DEFAULT_OPENOCD_EXEC_FILE_PATH)
    # print(DEFAULT_OPENOCD_SCRIPTS_PATH)

    result = 1

    if not os.path.exists(filename):
        print("ERROR: File %s does not exist" % filename)
        exit(1)

    segments: List[Segment] = read_file(filename)
    # print(segments)

    for segment in segments:
        if segment.memory is None:
            raise Exception(
                "ERROR: segment with offset %s doesn't belong to any section" % hex(segment.offset))

        if (segment.offset + segment.data.__len__()) > (segment.memory.offset + segment.memory.length):
            raise Exception("ERROR: segment with offset %s and length %s overflows section %s" % (
                hex(segment.offset), segment.data.__len__(), segment.memory.type.name))

    print(segments_to_pages(list(filter(
        lambda segment: (segment.memory is not None) and (segment.memory.type == MemoryType.EEPROM), segments)), 128))

    # proc: subprocess.Popen | None = None
    # if run_openocd:
    #     cmd = shlex.split("%s -s %s -f interface/ftdi/m-link.cfg -f target/mcu32.cfg" % (
    #         DEFAULT_OPENOCD_EXEC_FILE_PATH, DEFAULT_OPENOCD_SCRIPTS_PATH), posix=False)
    #     proc = subprocess.Popen(
    #         cmd, creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.SW_HIDE)

    # with OpenOcdTclRpc() as openocd:
    #     if segment_section.type == MemoryType.EEPROM:
    #         result = mik32_eeprom.write_words(bytes2words(
    #             segment.data), openocd, is_resume)
    #     elif segment_section.type == MemoryType.SPIFI:
    #         result = mik32_spifi.spifi_write_file(segment.data, openocd, is_resume)

    # if run_openocd and proc is not None:
    #     proc.kill()

    return result


def createParser():
    parser = argparse.ArgumentParser()
    parser.add_argument('filepath', nargs='?')
    parser.add_argument('--run-openocd', dest='run_openocd',
                        action='store_true', default=False)
    parser.add_argument(
        '--openocd-host', dest='openocd_host', default='127.0.0.1')
    parser.add_argument('--openocd-port', dest='openocd_port',
                        default=OpenOcdTclRpc.DEFAULT_PORT)
    # parser.add_argument('-b', '--boot-mode', default='undefined')

    return parser


if __name__ == '__main__':
    parser = createParser()
    namespace = parser.parse_args()

    if namespace.filepath:
        upload_file(namespace.filepath, namespace.openocd_host,
                    namespace.openocd_port, run_openocd=namespace.run_openocd)
    else:
        print("Nothing to upload")
