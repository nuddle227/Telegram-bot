class _B:
    def __init__(self,*a,**k): pass
    def __and__(self,o): return self
    def __invert__(self): return self
Application=CallbackQueryHandler=CommandHandler=MessageHandler=_B
class ContextTypes: DEFAULT_TYPE=None
class filters:
    TEXT=_B(); COMMAND=_B()
    class ChatType: PRIVATE=_B()
