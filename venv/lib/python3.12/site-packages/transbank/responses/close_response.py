from transbank.responses.load_keys_response import LoadKeysResponse


class CloseResponse(LoadKeysResponse):

    def __init__(self, response):
        super().__init__(response)