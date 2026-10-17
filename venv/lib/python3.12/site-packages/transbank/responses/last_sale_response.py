from transbank.responses.sale_response import SaleResponse


class LastSaleResponse(SaleResponse):

    def __init__(self, response):
        super().__init__(response)
