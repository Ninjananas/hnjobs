from typing import (
    List, Optional, Sequence, Dict, Tuple, Any,
    Generator, ClassVar, Callable, Union,
)
from enum import Enum
import os
from dataclasses import dataclass
from html.parser import HTMLParser
from collections import defaultdict
import json
from abc import ABCMeta, abstractmethod


try:
    import requests

    _session: requests.Session = requests.Session()

    def get_json(url: str) -> Optional[dict]:
        response = _session.get(url)
        if not (200 <= response.status_code < 300):
            return None
        return response.json()

except ImportError:
    from urllib import request

    def get_json(url: str) -> Optional[dict]:
        response = request.urlopen(url)
        if not (200 <= response.status < 300):
            return None
        return json.loads(response.read())


if os.name == "posix":
    import sys
    import tty
    import termios

    def getch() -> str:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
else:
    print("Sorry, but only posix systems are supported for now")
    exit(1)

############################################################


ENABLE_CACHE: bool = True  # Can't be disabled, will break things
PERSISTENT_CACHE: bool = True  # Everything is lost upon restart if False
HN_API_BASE_URL: str = "https://hacker-news.firebaseio.com/v0"
SAVE_FILE: str = "hnjobs.json"
COLORS: bool = True


class ItemType(str, Enum):
    JOB = "job"
    STORY = "story"
    COMMENT = "comment"
    POLL = "poll"
    POLLOPT = "pollopt"


@dataclass(kw_only=True, slots=False)
class HNItem(object):
    id: int
    type: ItemType
    time: int  # Unix timestamp
    title: Optional[str] = None
    text: Optional[str] = None  # In HTML
    parent: Optional[int] = None
    kids: Sequence[int] = tuple()
    descendants: Optional[int] = None
    deleted: Optional[bool] = None
    by: Optional[str] = None
    url: Optional[str] = None
    dead: Optional[bool] = None
    score: Optional[int] = None
    poll: Optional[int] = None
    parts: Optional[List[int]] = None


def _get_item(id_: int) -> Optional[HNItem]:
    dict_item = get_json(HN_API_BASE_URL + f"/item/{id_}.json")
    if dict_item is None:
        return None
    return HNItem(**dict_item)


_item_cache: Dict[int, HNItem] = {}


def _get_item_cached(id_: int) -> Optional[HNItem]:
    if (item := _item_cache.get(id_, None)) is not None:
        return item

    item = _get_item(id_)
    if item is not None:
        _item_cache[id_] = item

    return item


get_item = _get_item_cached if ENABLE_CACHE else _get_item


class CustomHTMLParser(HTMLParser):
    __slots__ = ("parts",)
    parts: List[str]

    def reset(self) -> None:
        self.parts = []
        super().reset()

    def get_text(self) -> str:
        return "".join(self.parts)

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Any]]):
        match tag:
            case "br":
                self.parts.append("\n")
            case "p":
                self.parts.append("\n\n")

    def handle_endtag(self, tag: str):
        pass

    def handle_data(self, data: str):
        self.parts.append(data)


def html_to_text(html: Optional[str]) -> str:
    if html is None:
        return ""
    parser = CustomHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.get_text()


def display_item(item: HNItem) -> None:
    os.system("clear")
    if item.title is not None:
        print(f"{item.title}\n")
    print(html_to_text(item.text))


def get_all_kids(base_item: HNItem) -> Generator[HNItem, None, None]:
    for id_ in base_item.kids:
        if (item := get_item(id_)) is not None:
            yield item


def command(arg: Union[Callable, str]) -> Callable:
    if callable(arg):
        key = arg.__name__[0]
    elif isinstance(arg, str):
        if len(arg) != 1:
            raise Exception("Command shortcuts must be a single character")
        key = arg

    def annotate_func(func: Callable) -> Callable:
        func.__shortcut__ = key
        return func

    if callable(arg):
        return annotate_func(arg)
    return annotate_func


class UserInterface(object, metaclass=ABCMeta):
    __slots__ = ("_run",)

    _tooltips_line: ClassVar[str]
    _tooltips_dict: ClassVar[Dict[str, Callable[[], None]]]
    _run: bool

    def __init__(self):
        super().__init__()
        self._run = True

    @staticmethod
    def register_command(
            dct: Dict[str, Callable],
            shortcut: str,
            command: Callable
    ) -> None:
        if shortcut in dct:
            shortcut = shortcut.swapcase()
        if shortcut in dct:
            raise Exception(f"Cannot register command {command}"
                            f" with shortcut {shortcut}")
        dct[shortcut] = command

    @classmethod
    def __init_subclass__(cls, /, **kwargs) -> None:
        super().__init_subclass__(**kwargs)

        ttd = {}
        for attr_name in dir(cls):
            try:
                attr = getattr(cls, attr_name)
            except AttributeError:
                continue
            if (sc := getattr(attr, "__shortcut__", None)) is not None:
                cls.register_command(ttd, sc, attr)
        cls._tooltips_dict = ttd

        tooltips: List[str] = []
        for k, v in ttd.items():
            name = v.__name__
            if k.lower() == name[0].lower():
                name = name[1:]
            tooltips.append(f"({k}){name}")
        cls._tooltips_line = " ".join(tooltips)

    def wait_command(self) -> None:
        while True:
            ch = getch()
            if ch not in self._tooltips_dict:
                continue
            break
        self._tooltips_dict[ch](self)

    @abstractmethod
    def update_display(self) -> str:
        raise NotImplementedError

    @classmethod
    def print_tooltips(cls) -> None:
        print(f"{cls._tooltips_line}\n\n")

    def refresh(self) -> None:
        os.system("clear")
        self.print_tooltips()
        print(self.update_display())

    def loop(self) -> None:
        while self._run:
            self.refresh()
            self.wait_command()

    def stop(self) -> None:
        self._run = False


_item_user_tags: Dict[int, List[str]] = defaultdict(list)
_item_user_ratings: Dict[int, int] = {}


class MainInterface(UserInterface):
    __slots__ = ("display",)

    def __init__(self):
        super().__init__()
        self.display = ""

    def update_display(self) -> str:
        return "Main interface\n\n" + self.display

    def display_now(self, s: str) -> None:
        self.display = s
        self.refresh()

    @command
    def quit(self) -> None:
        self.stop()

    @command
    def update_jobs(self) -> None:
        self.display_now("Please enter WhoIsHiring link or item id: ")
        i = input()
        try:
            i = i.split("id=")[-1]
            i = i.split("&")[0]
            id_ = int(i, 10)
        except Exception:
            self.display_now("Bad link or id")
            return
        self.display_now("Fetching...")
        item = get_item(id_)
        if not item:
            self.display_now("Could not fetch HN post!\n")
            return
        if item.by != "whoishiring":
            self.display_now("This does not seem to be a WhoIsHiring post!\n")
            return
        self.display_now(f"There are {len(item.kids)} comments here,"
                         " fetch them? [y/n]")

        while True:
            c = getch()
            if c.lower() == "n":
                return
            if c.lower() == "y":
                break
            self.display += "\nplease enter y or n"
            self.refresh()
        total = len(item.kids)
        n = 0
        self.display_now(f"fetching {total} items...")
        for id_ in item.kids:
            self.display_now(f"{n}/{total} comments fetched...")
            n += 1
            get_item(id_)
        self.display += "\ndone."

    @command
    def select_some_items(self) -> None:
        SelectorInterface().loop()


class InvalidFilterOrSorter(Exception):
    pass


FILTER_FUNCS = {
    "tag": lambda tag: lambda item: tag in _item_user_tags[item.id],
    "rated": lambda _: lambda item: item.id in _item_user_ratings,
    "contains": lambda s: lambda item: item.text and (s.lower() in item.text.lower()),
}


def filter_from_str(s: str) -> Callable:
    inverted = False
    if s.startswith("!"):
        inverted = True
        s = s[1:]
    parts = s.split(":")
    if len(parts) == 1:
        filter_name, arg = parts[0], None
    elif len(parts) == 2:
        filter_name, arg = parts
    else:
        raise InvalidFilterOrSorter("Too many ':'")

    try:
        func = FILTER_FUNCS[filter_name](arg)
        if inverted:
            return lambda x: not func(x)
        return func
    except Exception as e:
        raise InvalidFilterOrSorter(e)


SORTER_FUNCS = {
    "tag": lambda tag: lambda item: 0 if tag in _item_user_tags[item.id] else 1,
    "recent": lambda _: lambda item: -item.time,
    # It is strange to compare int with floats, but inf is quite useful here...
    "rating": lambda _: lambda item: -_item_user_ratings.get(item.id, float("-inf")),
    "contains": lambda s: lambda item: 0 if (item.text and (s.lower() in item.text.lower())) else 1,
}


def sorter_from_str(s: str) -> Callable:
    inverted = False
    if s.startswith("!"):
        inverted = True
        s = s[1:]
    parts = s.split(":")
    if len(parts) == 1:
        sorter_name, arg = parts[0], None
    elif len(parts) == 2:
        sorter_name, arg = parts
    else:
        raise InvalidFilterOrSorter("Too many ':'")

    try:
        func = SORTER_FUNCS[sorter_name](arg)
        if inverted:
            return lambda x: -func(x)
        return func
    except Exception as e:
        raise InvalidFilterOrSorter(e)


class SelectorInterface(UserInterface):
    __slots__ = "display", "filters", "sorters"

    display: str
    help: ClassVar[str] = (
        "Filter/sorter format: (!)<name>(:value)\n"
        "'!' inverts a filter/sorter\n"
        f"Available filters: {', '.join(FILTER_FUNCS.keys())}\n"
        f"Available sorters: {', '.join(SORTER_FUNCS.keys())}\n"
    )
    filters: List[str]
    sorters: List[str]

    def _summary(self) -> None:
        self.display = (
            f"{self.help}\n"
            f"Current filters: {self.filters}\n"
            f"Current sorters: {self.sorters}\n"
        )
        self.refresh()

    def __init__(self):
        super().__init__()
        self.filters = []
        self.sorters = []
        self._summary()

    def update_display(self) -> str:
        return self.display

    def display_now(self, s: str) -> None:
        self.display = s
        self.refresh()

    @command("f")
    def update_filters(self) -> None:
        new_filters_str = input("\nEnter new filters separated with ','\n")
        new_filters = new_filters_str.replace(" ", "").split(",")
        for f in new_filters:
            try:
                filter_from_str(f)
            except InvalidFilterOrSorter as e:
                self.display += f"Filter {f} is invalid: {e}"
                return
        self.filters = new_filters
        self._summary()

    @command("s")
    def update_sorters(self) -> None:
        new_sorters_str = input("\nEnter new sorters separated with ','\n")
        new_sorters = new_sorters_str.replace(" ", "").split(",")
        for s in new_sorters:
            try:
                sorter_from_str(s)
            except InvalidFilterOrSorter as e:
                self.display += f"Sorter {s} is invalid: {e}"
                return
        self.sorters = new_sorters
        self._summary()

    def _get_selected(self) -> List[HNItem]:
        items = filter(lambda item: item.type == ItemType.COMMENT, _item_cache.values())
        for f in self.filters:
            items = filter(filter_from_str(f), items)
        items = list(items)
        for s in self.sorters[::-1]:
            items.sort(key=sorter_from_str(s))
        return items

    @command
    def review_selected(self) -> None:
        self.stop()
        ReviewInterface(self._get_selected()).loop()

    @command
    def tag_selected(self) -> None:
        tag = input("\nEnter the tag to add to matching items: ")
        for item in self._get_selected():
            tags = _item_user_tags[item.id]
            if tag not in tags:
                tags.append(tag)

    @command
    def quit(self) -> None:
        self.stop()


class ReviewInterface(UserInterface):
    __slots__ = "current_index", "items"

    items: List[HNItem]
    current_index: int

    def __init__(self, items: List[HNItem]):
        self.items = items
        if not items:
            self.stop()
            return
        self.current_index = 0
        super().__init__()

    @property
    def current_item(self) -> HNItem:
        return self.items[self.current_index]

    def update_display(self) -> str:
        item = self.current_item
        return (
            f"Item {self.current_index + 1}/{len(self.items)}\n"
            f"Rating: {_item_user_ratings.get(item.id, '???')}\n"
            f"Tags: {_item_user_tags[item.id]}\n"
            "===============================================================\n"
            f"{html_to_text(item.text)}"
        )

    @command
    def next(self) -> None:
        self.current_index += 1
        self.current_index = min(len(self.items) - 1, self.current_index)

    @command
    def previous(self) -> None:
        self.current_index -= 1
        self.current_index = max(0, self.current_index)

    @command("t")
    def add_tags(self) -> None:
        tags = _item_user_tags[self.current_item.id]
        new_tags = input("Enter new tags separated by ',':\n").split("'")
        for tag in new_tags:
            if tag not in tags:
                tags.append(tag)

    @command
    def rate(self):
        try:
            rating = int(input("Enter new rating:\n"), 10)
            _item_user_ratings[self.current_item.id] = rating
        except ValueError:
            return

    @command
    def quit(self) -> None:
        self.stop()


def save() -> None:
    to_save = {
        "tags": _item_user_tags,
        "ratings": _item_user_ratings,
    }

    if PERSISTENT_CACHE:
        to_save["cache"] = dict(
            (k, v.__dict__) for k, v in _item_cache.items())

    json.dump(to_save, open(SAVE_FILE, "w"))


def load() -> None:
    global _item_user_tags
    global _item_user_ratings
    global _item_cache
    loaded: dict = json.load(open(SAVE_FILE, "r"))

    _item_user_tags = defaultdict(
        list,
        ((int(k), v) for k, v in loaded["tags"].items())
    )

    _item_user_ratings = dict(
        (int(k), v) for k, v in loaded["ratings"].items()
    )

    if PERSISTENT_CACHE:
        _item_cache = dict(
            (int(k, 10), HNItem(**v))
            for k, v in
            loaded.get("cache", {}).items())


def main() -> None:
    interface = MainInterface()
    interface.loop()


if __name__ == "__main__":
    try:
        load()
    except FileNotFoundError:
        pass

    try:
        main()

    except KeyboardInterrupt:
        pass

    finally:
        save()
