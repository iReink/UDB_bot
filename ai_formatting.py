"""Escaped Markdown subset shared by grounded answers in Telegram and the web."""
from html import escape
from html.parser import HTMLParser
import re

INLINE = re.compile(r'`[^`\n]+`|\[[^\]\n]+\]\(https://[^\s)]+\)|\*\*(\S(?:.*?\S)?)\*\*|__(\S(?:.*?\S)?)__|(?<!\w)\*(\S(?:[^*\n]*?\S)?)\*(?!\w)|(?<!\w)_(\S(?:[^_\n]*?\S)?)_(?!\w)|\[\d+\]')


def inline(text, sources, web=False, depth=0, links=True):
    from ai_grounding import safe_url
    if depth>6:return escape(text)
    output=[];position=0
    for match in INLINE.finditer(text):
        output.append(escape(text[position:match.start()]))
        token=match.group();rendered=escape(token)
        if token.startswith('`'):rendered='<code>'+escape(token[1:-1])+'</code>'
        elif token.startswith(('**','__')):
            rendered='<b>'+inline(token[2:-2],sources,web,depth+1,links)+'</b>'
        elif token.startswith(('*','_')):
            rendered='<i>'+inline(token[1:-1],sources,web,depth+1,links)+'</i>'
        else:
            citation=re.fullmatch(r'\[(\d+)\]',token)
            if citation:
                index=int(citation[1]);url=sources[index-1].get('url') if 0<index<=len(sources) else None
                label=escape(token)
            else:
                label,url=token[1:].split('](',1);url=url[:-1]
                label=inline(label,sources,web,depth+1,False)
            url=safe_url(url)
            if links and url and (web or len(url)<1600):
                attrs=' target="_blank" rel="noopener noreferrer"' if web else ''
                rendered='<a href="'+escape(url,quote=True)+'"'+attrs+'>'+label+'</a>'
            elif not citation:rendered=label
        output.append(rendered);position=match.end()
    output.append(escape(text[position:]));return ''.join(output)


def answer_html(text, sources=(), web=False):
    # Internal model citation labels aren't the numbered API source contract.
    if sources:text=re.sub(r'\[\d+\.\d+(?:\s*,\s*\d+\.\d+)*\]','',text)
    blocks=[];paragraph=[];items=[];list_kind=None;code=None
    def flush_paragraph():
        if paragraph:
            content=inline('\n'.join(paragraph),sources,web)
            blocks.append('<p>'+content.replace('\n','<br>')+'</p>' if web else content)
            paragraph.clear()
    def flush_list():
        nonlocal list_kind
        if items:
            blocks.append('<'+list_kind+'>'+''.join('<li>'+content+'</li>' for _,content in items)+'</'+list_kind+'>' if web else '\n'.join(prefix+content for prefix,content in items))
            items.clear();list_kind=None
    for line in text.replace('\r\n','\n').split('\n'):
        if line.lstrip().startswith('```'):
            if code is None:flush_paragraph();flush_list();code=[]
            else:blocks.append('<pre>'+escape('\n'.join(code))+'</pre>');code=None
            continue
        if code is not None:code.append(line);continue
        heading=re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*$',line)
        item=re.match(r'^\s*(?:([-*+])\s+|(\d+)[.)]\s+)(.*)$',line)
        if heading:
            flush_paragraph();flush_list();value=inline(heading[1],sources,web)
            blocks.append('<h2>'+value+'</h2>' if web else '<b>'+value+'</b>')
        elif item:
            flush_paragraph();kind='ul' if item[1] else 'ol'
            if list_kind and list_kind!=kind:flush_list()
            list_kind=kind;items.append(('• ' if item[1] else item[2]+'. ',inline(item[3],sources,web)))
        elif not line.strip():flush_paragraph();flush_list()
        else:flush_list();paragraph.append(line)
    flush_paragraph();flush_list()
    if code is not None:blocks.append('<pre>'+escape('\n'.join(code))+'</pre>')
    return ('\n' if web else '\n\n').join(blocks)


class TelegramChunks(HTMLParser):
    """Split only generated HTML, closing/reopening tags at message boundaries."""
    def __init__(self,limit):
        super().__init__(convert_charrefs=True)
        self.limit=limit;self.stack=[];self.parts=[];self.current='';self.has_text=False

    @staticmethod
    def size(value):return len(value.encode('utf-16-le'))//2

    def closing(self):return ''.join(end for _,end in reversed(self.stack))

    def flush(self):
        if self.has_text:self.parts.append(self.current+self.closing())
        self.current=''.join(start for start,_ in self.stack);self.has_text=False

    def handle_starttag(self,tag,attrs):
        start=self.get_starttag_text();end='</'+tag+'>'
        if self.size(self.current+start+self.closing()+end)>self.limit:self.flush()
        self.current+=start;self.stack.append((start,end))

    def handle_endtag(self,tag):
        self.current+='</'+tag+'>';self.stack.pop()

    def handle_data(self,data):
        for token in re.split(r'(\s+)',data):
            encoded=escape(token)
            if self.size(self.current+encoded+self.closing())>self.limit:
                self.flush()
            if self.size(self.current+encoded+self.closing())<=self.limit:
                self.current+=encoded;self.has_text|=bool(token)
            else:
                for character in token:
                    encoded=escape(character)
                    if self.size(self.current+encoded+self.closing())>self.limit:self.flush()
                    self.current+=encoded;self.has_text=True


def telegram_chunks(html,limit=3900):
    parser=TelegramChunks(limit);parser.feed(html);parser.close();parser.flush()
    return parser.parts
