from transbank.responses.intermediate_message_response import IntermediateMessageResponse


class LoadKeysResponse(IntermediateMessageResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['success'] = self.success
        self.parsed_response['commerce_code'] = self.split_response[2]
        self.parsed_response['terminal_id'] = self.split_response[3]

