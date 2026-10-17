from transbank.responses.load_keys_response import LoadKeysResponse


class RefundResponse(LoadKeysResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['authorization_code'] = self.split_response[4]
        self.parsed_response['operation_number'] = self.split_response[5]
