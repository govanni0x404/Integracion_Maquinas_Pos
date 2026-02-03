from transbank.responses.load_keys_response import LoadKeysResponse


class SaleResponse(LoadKeysResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['ticket'] = self.split_response[4]
        self.parsed_response['authorization_code'] = self.split_response[5]
        self.parsed_response['amount'] = self.split_response[6]
        self.parsed_response['shares_number'] = self.split_response[7]
        self.parsed_response['shares_amount'] = self.split_response[8]
        self.parsed_response['last_4_digits'] = self.split_response[9]
        self.parsed_response['operation_number'] = self.split_response[10]
        self.parsed_response['card_type'] = self.split_response[11]
        self.parsed_response['accounting_date'] = self.split_response[12]
        self.parsed_response['account_number'] = self.split_response[13]
        self.parsed_response['card_brand'] = self.split_response[14]
        self.parsed_response['real_date'] = self.split_response[15]
        self.parsed_response['real_time'] = self.split_response[16]
        self.parsed_response['employee_id'] = self.split_response[17]
        self.parsed_response['tip'] = self.split_response[18]
