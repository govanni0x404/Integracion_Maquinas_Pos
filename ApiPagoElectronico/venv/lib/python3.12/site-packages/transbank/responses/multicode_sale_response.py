from transbank.responses.sale_response import SaleResponse


class MultiCodeSaleResponse(SaleResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['change'] = self.split_response[20]
        self.parsed_response['child_commerce_code'] = self.split_response[21]


