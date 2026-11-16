class TelegramError(Exception): pass
class BadRequest(TelegramError): pass
class Forbidden(TelegramError): pass
