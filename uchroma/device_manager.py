import logging

import hidapi

from pyudev import Context, Monitor, MonitorObserver

from uchroma.device import UChromaDevice
from uchroma.models import Model, RAZER_VENDOR_ID


class UChromaDeviceManager(object):
    """
    Enumerates HID devices which can be managed by uChroma

    This is the main API entry point when developing applications
    with uChroma. Simply instantiate this object and the
    available devices can be fetched from the "devices" dict.

    Uses HIDAPI for low-level hardware interactions. Suitable
    permissions are required on the device nodes or this will
    fail.
    """

    def __init__(self, *callbacks):
        self._logger = logging.getLogger('uchroma.devicemanager')

        self._devices = {}
        self._monitor = False
        self._udev_context = Context()
        self._udev_observer = None
        self._callbacks = []

        if callbacks is not None:
            self._callbacks.extend(callbacks)

        self.discover()


    def _fire_callbacks(self, action, device):
        for callback in self._callbacks:
            callback(action, device)


    def discover(self):
        """
        Perform HID device discovery

        Iterates over all connected HID devices with RAZER_VENDOR_ID
        and checks the product ID against the Model enumeration.

        Interface endpoint restrictions are currently hard-coded. In
        the future this should be done by checking the HID report
        descriptor of the endpoint, however this functionality in
        HIDAPI is broken (report_descriptor returns garbage) on
        Linux in the current release.

        Discovery is automatically performed when the object is
        constructed, so this should only need to be called if the
        list of devices changes (monitoring for changes is beyond
        the scope of this API).
        """
        devinfos = hidapi.enumerate(vendor_id=RAZER_VENDOR_ID)
        for devinfo in devinfos:
            for devtype in Model:
                if devinfo.product_id in devtype.value:
                    add = True
                    if devtype == Model.KEYBOARD or devtype == Model.LAPTOP:
                        if devinfo.interface_number != 2:
                            add = False
                    elif devtype == Model.MOUSEPAD:
                        if devinfo.interface_number != 1:
                            add = False
                    else:
                        if devinfo.interface_number != 0:
                            add = False

                    if add:
                        pid = '%04x' % devinfo.product_id
                        key = '%04x:%s' % (devinfo.vendor_id, pid)
                        if key in self._devices:
                            continue

                        self._devices[key] = UChromaDevice(
                            devinfo, devtype.name, devtype.value[devinfo.product_id],
                            self._get_input_devices(self._get_parent(pid)))

                        self._fire_callbacks('add', self._devices[key])


    @property
    def devices(self):
        """
        Dict of available devices, empty if no devices are detected.
        """
        return self._devices


    @property
    def callbacks(self):
        """
        List of callbacks invoked when device changes are detected
        """
        return self._callbacks


    def _get_parent(self, product_id: str):
        devs = self._udev_context.list_devices(tag='uchroma', subsystem='usb',
                                               ID_MODEL_ID=product_id)
        for dev in devs:
            if dev['DEVTYPE'] == 'usb_device':
                return dev

        return None


    def _get_input_devices(self, parent) -> list:
        inputs = []
        if parent is not None:
            for child in parent.children:
                if child.subsystem == 'input' and 'DEVNAME' in child:
                    inputs.append(child['DEVNAME'])

        return inputs


    def _udev_event(self, device):
        self._logger.debug('Device event [%s]: %s', device.action, device.device_path)

        if device.action == 'remove':
            key = '%s:%s' % (device['ID_VENDOR_ID'], device['ID_MODEL_ID'])
            removed = self._devices.pop(key, None)
            if removed is not None:
                removed.close()
                self._fire_callbacks('remove', removed)

        else:
            self.discover()


    def monitor_start(self):
        """
        Start watching for device changes

        Listen for relevant add/remove events from udev and fire callbacks.
        """

        if self._monitor:
            return

        udev_monitor = Monitor.from_netlink(self._udev_context)
        udev_monitor.filter_by_tag('uchroma')
        udev_monitor.filter_by(subsystem='usb', device_type=u'usb_device')

        self._udev_observer = MonitorObserver(udev_monitor, callback=self._udev_event,
                                              name='uchroma-monitor')
        self._udev_observer.start()
        self._monitor = True

        self._logger.debug('Udev monitor started')


    def monitor_stop(self):
        """
        Stop watching for device changes
        """
        if not self._monitor:
            return

        self._udev_observer.send_stop()
        self._monitor = False

        self._logger.debug('Udev monitor stopped')

