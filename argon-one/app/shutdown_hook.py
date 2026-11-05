#!/usr/bin/python3
# Runs on the host from systemd-shutdown, so it only uses the standard library.
# Same sequence as Argon's argon-shutdown.sh: fan off, then ask the MCU to cut power on poweroff/halt.
# "check" only reports whether the MCU supports control registers.
import fcntl, os, sys, time

I2C_SLAVE = 0x0703
ADDR = 0x1a
REG_DUTYCYCLE = 0x80
REG_CTRL = 0x86

fd = os.open("/dev/i2c-1", os.O_RDWR)
fcntl.ioctl(fd, I2C_SLAVE, ADDR)


def setbyte(reg, val):
    os.write(fd, bytes([reg, val]))
    time.sleep(1)


def getbyte(reg):
    os.write(fd, bytes([reg]))
    return os.read(fd, 1)[0]


def regsupport():
    old = getbyte(REG_DUTYCYCLE)
    new = 98 if old + 1 >= 100 else old + 1
    setbyte(REG_DUTYCYCLE, new)
    if getbyte(REG_DUTYCYCLE) != old:
        setbyte(REG_DUTYCYCLE, old)
        return True
    return False


action = sys.argv[1] if len(sys.argv) > 1 else ""
supported = regsupport()
if action == "check":
    print("regsupport", supported, "duty", getbyte(REG_DUTYCYCLE))
    sys.exit(0)
if action:
    if supported:
        setbyte(REG_DUTYCYCLE, 0)
    else:
        os.write(fd, bytes([0]))
        time.sleep(1)
    if action in ("poweroff", "halt"):
        if supported:
            setbyte(REG_CTRL, 1)
        else:
            os.write(fd, bytes([0xFF]))
