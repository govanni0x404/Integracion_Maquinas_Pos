from transbank.responses.response_codes import response_codes


class BaseResponse(object):
    STX = '\x02'
    ETX = '\x03'

    def __init__(self, response: bytes):
        self.decoded_response = response.decode()
        self.split_response = self.decoded_response.replace(self.STX, '').replace(self.ETX, '|').split("|")
        self.response_message = response_codes.get(int(self.split_response[1]), 'Code not found')
        self.success = True if self.split_response[1] == "00" else False
        self.parsed_response = dict()

    def get_response(self):

        return self.parsed_response
