#!/bin/usr/python3
#pylint: disable=C0103,W0401,R0903,C0321,W0201,W0231,C0111,C0330,C0326
"""Scraps data from the isaac API doc and copies it in the target file.
The script takes in argument the path to the Afterbirth API docs."""

import re
import os
import os.path as pth
import sys
from typing import (
    Iterator, Tuple, List, Match, cast,
    Optional, Union, NamedTuple, IO, Callable )

from scraper_regexs import *

def tryMatch(x: Match, prop: str) -> bool:
    """Convinience function to test existance of a group in match object"""
    try:
        return x.group(prop) is not None
    except (IndexError, AttributeError):
        return False

def tryMatchNone(x: Optional[Match], prop: str) -> Optional[str]:
    """Convinience function to test existance of a group in an U[Match,None]."""
    if x is None:
        return None
    else:
        return x.group(prop)

def tryMatchString(x: Optional[Match], prop: str) -> str:
    """Like tryMatchNone but returns '' instead of None"""
    if x is None:
        return ''
    else:
        ret = x.group(prop)
        return ret if ret is not None else ''


class InvalidRematcher(Exception):
    """Rose when trying to create an object from an invalid matcher."""
    def __init__(self,
                 msg: str= 'Couldn\'t construct Class from matcher') -> None:
        super().__init__()
        self.message = msg

class InvalidFunctionRematcher(InvalidRematcher):
    def __init__(self,
                 msg: str= 'Couldn\'t construct function form line') -> None:
        super().__init__()
        self.message = msg

class InvalidAttributeRematcher(InvalidRematcher):
    def __init__(self,
                 msg: str= 'Couldn\'t construct attribute form line') -> None:
        super().__init__()
        self.message = msg

class UpdatedDocError(Exception):
    """Rose when the script detects that the Afterbirth API doc structure
    changed."""
    def __init__(self,
                 msg: str= 'This script is probably not compatible with the \
                 current Afterbirth+ doc API, please see what you \
                 can do') -> None:
        super().__init__()
        self.message = msg


class DocDescription:
    """Holds the description and link to the documentation entry."""
    description = None #type: str
    link = None #type: Optional[str]

    def __init__(self, description: str, docLink: Optional[str]) -> None:
        self.description = description
        # TODO:
        # once https://github.com/atom/autocomplete-plus/pull/763 merged, use:
        # self.link = 'file://' + docLink if docLink else None
        # instead
        self.link = 'https://moddingofisaac.com/docs/' + pth.basename(docLink)\
                        if docLink else None


class LuaType:
    """A simple lua type description.
    The constructor provides means to interpolate the type from the docs."""
    name = None #type: str
    isConst = None #type: bool
    isStatic = None #type: bool

    def __init__(self,
                 typeHint: Union[Match, str]= None,
                 isConst: bool= False,
                 isStatic: bool= False) -> None:
        if typeHint is None:
            self.__initFlat('nil')
        elif not isinstance(typeHint, str):
            self.__initFlat(
                typeHint.group('type'),
                tryMatch(typeHint, 'const'),
                tryMatch(typeHint, 'static') )
        else:
            self.__initFlat(typeHint, isConst, isStatic)

    def __initFlat(self,
                   name: str='', isConst: bool= False, isStatic: bool= False):
        self.name = name if name != '' else 'nil' # type: str
        self.isConst = isConst # type: bool
        self.isStatic = isStatic # type: bool


class LuaVariable:
    """A lua variable, inherited by classes describing attributes and other."""
    name = None #type: str
    luaType = None #type: LuaType

    def __init__(self, name: str, luaType: LuaType) -> None:
        self.luaType = luaType
        if name == '':
            name = self.luaType.name
        self.name = name


class LuaParam(LuaVariable):
    """A parameter of a Lua function."""
    def __init__(self, line: str) -> None:
        """Initializes the parameter interpolating from line."""
        paramMatcher = RE_FUNCTION_PARAMETER.search(line)
        super().__init__(
            tryMatchString(paramMatcher, 'name'),
            LuaType(paramMatcher) )


class LuaAttribute(LuaVariable):
    """An attribute of a Lua class.
    The constructor can interpolate from the doc strings"""
    # super()
    description = None #type: DocDescription

    def __init__(self, line: str) -> None:
        """Initializes the attribute using a reg-exed line."""
        attribScraper = RE_ATTRIBUTE.search(line)
        name  = tryMatchNone(attribScraper, 'name')
        if name is None:
            raise InvalidAttributeRematcher()
        self.name = name # type: str
        typeFetcher = RE_ATTRIBUTE_TYPE.search(
            RE_HTML_REPLACER.sub('', attribScraper.group('type')))
        self.luaType = LuaType(typeFetcher) # type: LuaType


class LuaFunction:
    """A structure to hold information about lua functions."""
    name = None #type: str
    description = None #type: DocDescription
    parameters = [] #type: List[LuaParam]
    returnType = None #type: LuaType

    def __init__(self, arg: Union[Match, str]) -> None:
        """Initializes a function with a re.Match object or a string
        to parse."""
        if not isinstance(arg, str):
            arg = cast(Match, arg)
            self.__initMatch(arg)
        else:
            self.__initLine(arg)

    def __initLine(self, line: str):
        functionSignMatcher = RE_FUNCTION_SIGNATURE.search(line)
        if functionSignMatcher is not None:
            self.__initMatch(functionSignMatcher)
        else:
            raise InvalidFunctionRematcher()

    def __initMatch(self, functionRematcher: Match):
        if functionRematcher is None:
            raise InvalidFunctionRematcher()
        self.name = functionRematcher.group('name') # type: str
        self._findParameters(functionRematcher.group('parameters'))
        self._findReturnval(functionRematcher.group('returns'))

    def _findParameters(self, paramMatchedVals: str):
        """Finds the parameters and adds them to instance."""
        if not paramMatchedVals: return
        paramScraper = RE_HTML_REPLACER.sub('', paramMatchedVals).split(', ')
        self.parameters = [LuaParam(param) for param in paramScraper if param]

    def _findReturnval(self, retMatchval: str):
        """Finds the function return value in retMatchval."""
        if not retMatchval:
            self.returnType = LuaType() # type: LuaType
        else:
            returnMatcher = RE_FUNCTION_RETURNS.search(
                RE_HTML_REPLACER.sub('', retMatchval))
            self.returnType = LuaType(returnMatcher) # type: LuaType


class LuaMethod(LuaFunction):
    """Extends lua functions to account for class constructors."""
    def __init__(self, args: Union[Match, str], class_: 'LuaClass') -> None:
        super().__init__(args)
        if self.name == class_.name:
            self.returnType = LuaType(class_.name, isStatic= True)
            class_.constructor = self


def _parseDescription(line: str) -> Optional[str]:
    """Returns a string if it parsed a description in line.
    Returns None otherwise."""
    try:
        description = subHtmlFlags(
            RE_DESCRIPTION.search(line).group(1).strip())
    except AttributeError:
        return None
    else:
        return description


class LuaClass:
    """Describes a lua class."""
    parents = None #type: List[LuaClass]
    name = None #type: str
    description = None #type: DocDescription # Unused
    methods = [] #type: List[LuaMethod]
    attributes = [] #type: List[LuaAttribute]
    constructor = None #type: LuaMethod

    def __init__(self, classPath: str) -> None:
        """Initializes the class based on infos gathered in the class file."""
        with open(classPath, 'r') as classFile:
            content = classFile.read()
            self.name = RE_CLASS_NAME.search(content).group(1) # type: str
            self.description = DocDescription(self.name + ' instance',
                                              classPath)
            self._parentNames = [] # type: List[str]
            self.attributes = [] # type: List[LuaAttribute]
            self.methods = [] # type: List[LuaMethod]

            # Currently, inheritance has no purpose.
            inheritedMatcher = RE_INHERITS_FROM.search(content)
            if inheritedMatcher is not None:
                self._parentNames += [inheritedMatcher.group(1)]
            del inheritedMatcher
            del content

            classFile.seek(0)
            METHOD_SET = 1 #flags to decide to whome
            ATTRIB_SET = 2 #the last description must
            lastSet = 0 # be attributed
            for curLine in classFile:
                try: #try to find a function.
                    self.methods += [LuaMethod(curLine, self)]
                except InvalidFunctionRematcher: pass
                else: lastSet = METHOD_SET

                try: #try to find an attribute.
                    self.attributes += [LuaAttribute(curLine)]
                except InvalidAttributeRematcher: pass
                else: lastSet = ATTRIB_SET

                # finds description for previously found field of class
                lineDescr = _parseDescription(curLine)
                if lineDescr is not None:
                    if lastSet == METHOD_SET:
                        self.methods[-1].description = \
                            DocDescription(lineDescr, classPath)
                    elif lastSet == ATTRIB_SET:
                        self.attributes[-1].description = \
                            DocDescription(lineDescr, classPath)


class LuaNamespace:
    """Describes a lua namespace."""
    name = None #type: str
    functions = None #type: List[LuaFunction]

    def __init__(self, namespacePath: str) -> None:
        """Initializes the namespace descibed in the file namespacePath."""
        with open(namespacePath, 'r') as namespaceFile:
            content = namespaceFile.read()
            try:
                self.name = RE_NAMESPACE_NAME.search(content).group(1)
            except AttributeError as e: #HACK: global functions are in global
                if pth.basename(namespacePath) == 'group__funcs.html':
                    self.name = '_G'
                else:
                    raise e
            self.functions = [] # type: List[LuaFunction]
            del content

            namespaceFile.seek(0)
            for curLine in namespaceFile:
                try: #try to find a function
                    self.functions += [LuaFunction(curLine)]
                except InvalidFunctionRematcher: pass

                lineDescr = _parseDescription(curLine)
                if lineDescr is not None:
                    self.functions[-1].description = \
                        DocDescription(lineDescr, None)


EnumTag = NamedTuple('EnumTag', [
    ('name', str),
    ('value', int),
    ('description', str)
])


class LuaEnumerator:
    """A lua enumerator, contains all its members."""
    name = None # type: str
    members = None # type: List[EnumTag]
    streamInit = None # type: Callable[[IO],Optional[LuaEnumerator]]
    description = None # type: DocDescription

    def __init__(self, name: str, link: str, members: List[EnumTag]) -> None:
        """Initializes the class as a struct."""
        self.name = name
        self.members = members
        self.description = DocDescription('Enum ' + name, link)


def __streamInit(openFile: IO) -> Optional[LuaEnumerator]:
    """Reads the text stream for a lua enumerator.

    It will reads the stream untill it completes a LuaEnumerator,
    it will then return it. Note that the IO pointer in argument will
    be modified by this function."""
    curMemberList = [] # type: List[EnumTag]
    curEnumName = None # type: str
    fileName = openFile.name

    oldPointerPosition = openFile.tell()
    curLine = openFile.readline() # type: str
    while curLine != '':
        enumNameScraper = RE_ENUM_NAME.search(curLine)
        if enumNameScraper is not None: # We find the enumerator specification
            if curEnumName is None:
                curEnumName = enumNameScraper.group('name')
                curEnumLink = enumNameScraper.group('link')
            else:
                openFile.seek(oldPointerPosition) # unconsumes last line
                return LuaEnumerator(curEnumName,
                                     fileName + '#' + curEnumLink,
                                     curMemberList)
        else:
            memberScraper = RE_ENUM_MEMBER.search(curLine)
            if memberScraper is not None: # We find an enum field specification
                descripString = tryMatchString(memberScraper, 'desc')
                curMemberList += [EnumTag(
                    memberScraper.group('name'),
                    0, # the value enumTag field might be pertinent one day
                    RE_HTML_REPLACER.sub('', descripString)
                )]
        oldPointerPosition = openFile.tell()
        curLine = openFile.readline()
    # reached end of file
    if curEnumName is not None:
        return LuaEnumerator(curEnumName, curEnumLink, curMemberList)
    else:
        return None
LuaEnumerator.streamInit = __streamInit # add the method to the class as static


def allDocFiles(docPath: str) -> Iterator[Tuple[str, str]]:
    """List all files in the docPath.

    Returns their file name and associated directory paths.
    Note: currently the doc only has one level deep file organization,
    and this function only works if it is the case.
    If this ever came to change, this function needs update."""
    for dirEntry in os.scandir(docPath):
        if dirEntry.is_dir():
            try: assert dirEntry.name == 'search'
            except AssertionError: raise UpdatedDocError

            for searchFile in os.scandir(dirEntry.path):
                yield searchFile.name, dirEntry.path
        else:
            yield dirEntry.name, docPath


def categorizeFiles(docPath: str) \
        -> Tuple[List[str], List[str], List[str], List[str]]:
    """Lists all pertinent files in the documentation.
    Returns: class-files, namespace-files, enumerator-files"""

    def isClassFile(name: str) -> bool: #filter for class-description files
        """Returns True if name matches a class description file."""
        return re.fullmatch(r'class_[0-9A-Za-z_]*(?!-members.html)\.html',
                            name) is not None
    def isNamespaceFile(name: str) -> bool:
        """Filter for namespace descripting files."""
        return re.fullmatch(r'namespace_[\w]+\.html', name) is not None
    return (
        [pth.join(curDir, curFile) for curFile, curDir in allDocFiles(docPath)
                                   if isClassFile(curFile)],
        [pth.join(curDir, curFile) for curFile, curDir in allDocFiles(docPath)
                                   if isNamespaceFile(curFile)],
        [pth.join(docPath, 'group__enums.html')],
        [pth.join(docPath, 'group__funcs.html')] )


class AfterbirthApi:
    """Holds all the informations about the API."""
    classes = [] # type: List[LuaClass]
    enumerators = [] # type: List[LuaEnumerator]
    namespaces = [] # type: List[LuaNamespace]

    def __init__(self, docPath: str) -> None:
        classFiles, nsFiles, enumFiles, funFiles = categorizeFiles(docPath)

        self.classes = [LuaClass(f) for f in classFiles]
        self.namespaces = [LuaNamespace(f) for f in nsFiles + funFiles]
        for curFile in enumFiles:
            with open(curFile, 'r') as enumStream:
                while True: #do while, breaks when reached end of stream
                    curEnum = LuaEnumerator.streamInit(enumStream)
                    if curEnum is None:
                        break
                    self.enumerators += [curEnum]


if __name__ == '__main__':
    argDocPath = ' '.join(sys.argv[1:])
    abpApi = AfterbirthApi(argDocPath)
    print(abpApi)
