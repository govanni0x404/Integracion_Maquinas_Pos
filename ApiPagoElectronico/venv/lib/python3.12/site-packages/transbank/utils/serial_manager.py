import serial.tools.list_ports
import serial
import time

from transbank.error.transbank_exception import TransbankException
from transbank.responses.intermediate_message_response import IntermediateMessageResponse


class Serial:

    __DEFAULT_TIMEOUT = 150000
    __DEFAULT_BAUD_RATE = 115200
    _serial_port = None
    __ACK = b'\x06'
    __STX = '\x02'
    __ETX = '\x03'
    __timeout = __DEFAULT_TIMEOUT

    @property
    def timeout(self):
        return self.__timeout

    @timeout.setter
    def timeout(self, timeout):
        self.__timeout = timeout

    @staticmethod
    def list_ports():
        """
        List available COM ports.
        :return:
        list
            compound of a dict for each port {"port": xxx, "description": xxx}
        """
        serial_ports = serial.tools.list_ports.comports()
        ports = []
        for port, description, hwid in serial_ports:
            ports.append({"port": port, "description": description})
        return ports

    def open_port(self, port: str, baud_rate=__DEFAULT_BAUD_RATE):
        """
        Open a COM port.
        :param port: str
            Device name
        :param baud_rate: int, default 115200
            Rate at which information is transferred to port
        :return:
        bool
            True if port was opened
        """
        self._serial_port = serial.Serial(port=port, baudrate=baud_rate)
        return self._serial_port.isOpen()

    def close_port(self):
        """
        Close a COM port previously opened
        :return:
        bool
            True if port was closed
        """
        self._serial_port.close()
        return not self._serial_port.isOpen()

    def _can_write(self):
        if self._serial_port is None or not self._serial_port.isOpen():
            raise TransbankException("Can't write to port, the port is null or not open")

    def _create_command(self, payload: str):
        calculated_lrc = self.__lrc(payload+self.__ETX)
        full_command = [ord(self.__STX)]
        for character in payload:
            full_command.append(ord(character))
        full_command.append(ord(self.__ETX))
        full_command.append(ord(calculated_lrc))
        return full_command

    @staticmethod
    def __lrc(command: str):
        lrc = 0
        for character in command:
            lrc = lrc ^ ord(character)
        return chr(lrc)

    def _check_ack(self):
        self.__wait_response()
        response = self._serial_port.read()
        return response == self.__ACK

    def __wait_response(self):
        timer = 0
        while timer < self.__timeout:
            if self._serial_port.inWaiting() > 0:
                break
            time.sleep(0.2)
            timer += 1
        if timer == self.__timeout:
            self._serial_port.flushInput()
            raise TransbankException("Read operation Timeout")

    def _send_command(self, command, intermediate_messages=False, sales_detail=False, print_on_pos=False, callback=None):
        self._can_write()
        full_command = self._create_command(command)
        self._serial_port.flush()
        self._serial_port.write(full_command)
        if not self._check_ack():
            raise TransbankException("NACK received, check the message sent to the POS")

        response = self.__read_response()

        if intermediate_messages:
            while self.__is_intermediate_message(response):
                intermediate_response = IntermediateMessageResponse(response)
                callback(intermediate_response.get_response())
                response = self.__read_response()

        if sales_detail and print_on_pos:
            details_response = [response]
            while self.__has_authorization_code(response):
                response = self.__read_response()
                details_response.append(response)
            return details_response

        return response

    def __read_response(self):
        self.__wait_response()
        bytes_in_waiting = self._serial_port.inWaiting()
        response = self._serial_port.read(bytes_in_waiting)
        self.__send_ack()
        while response.decode()[-2] != self.__ETX:
            self.__wait_response()
            bytes_in_waiting = self._serial_port.inWaiting()
            response += self._serial_port.read(bytes_in_waiting)
        return response

    def __send_ack(self):
        ack = [ord(self.__ACK)]
        self._serial_port.write(ack)

    def __has_authorization_code(self, response: bytes):
        parsed_response = response.decode().replace(self.__STX, '').split("|")
        return not (parsed_response[5] == "" or parsed_response[5] == " ")

    def __is_intermediate_message(self, response: bytes):
        parsed_response = response.decode().replace(self.__STX, '').split("|")
        return parsed_response[0] == "0900"

