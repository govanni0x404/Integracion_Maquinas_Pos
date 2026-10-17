class TransbankException(Exception):
    def __int__(self, message="An error has occurred, verify given parameters."):
        super().__init__(message)
