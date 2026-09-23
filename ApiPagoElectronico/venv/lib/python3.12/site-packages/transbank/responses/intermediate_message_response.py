from transbank.responses.base_response import BaseResponse


class IntermediateMessageResponse(BaseResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['function_code'] = int(self.split_response[0])
        self.parsed_response['response_code'] = self.split_response[1]
        self.parsed_response['response_message'] = self.response_message
