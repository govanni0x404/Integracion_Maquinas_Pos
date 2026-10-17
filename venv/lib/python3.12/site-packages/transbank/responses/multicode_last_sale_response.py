from transbank.responses.sale_response import SaleResponse


class MultiCodeLastSaleResponse(SaleResponse):

    def __init__(self, response):
        super().__init__(response)
        voucher_data = self.split_response[19]
        voucher = []
        for x in range(0, len(voucher_data), 40):
            voucher.append(voucher_data[x:x+40])

        self.parsed_response['voucher'] = voucher
        self.parsed_response['change'] = self.split_response[20]
        self.parsed_response['child_commerce_code'] = self.split_response[21]
