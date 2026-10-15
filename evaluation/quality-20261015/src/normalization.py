import unicodedata

def normalize_words(text):
 text=unicodedata.normalize('NFKC',str(text)).casefold().replace('\u2019',"'").replace('\u2018',"'")
 text=''.join(c if c.isalnum() or c=="'" else ' ' for c in text)
 return [x for t in text.split() if (x:=t.strip("'"))]
