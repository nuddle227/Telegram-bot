class _B:
    def __init__(self,*a,**k): self.a=a; self.k=k
class InlineKeyboardButton(_B):
    def __init__(self,text,callback_data=None): self.text=text; self.callback_data=callback_data
class InlineKeyboardMarkup(_B):
    def __init__(self,rows): self.inline_keyboard=tuple(tuple(r) for r in rows)
class InputMediaPhoto(_B): pass
class Message(_B): pass
class Update(_B):
    ALL_TYPES=None
