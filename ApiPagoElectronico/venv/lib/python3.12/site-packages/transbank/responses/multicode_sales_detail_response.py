from transbank.responses.sales_detail_response import SalesDetailResponse


class MultiCodeSalesDetailResponse(SalesDetailResponse):

    def __init__(self, response):
        super().__init__(response)
        self.parsed_response['change'] = self.split_response[19]
        self.parsed_response['child_commerce_code'] = self.split_response[20]
