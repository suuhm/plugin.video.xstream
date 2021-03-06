#!/usr/bin/env python2.7
import hashlib
import httplib
import os
import socket
import sys
import time
import urllib

import mechanize
import xbmcgui

from resources.lib import common
from resources.lib import logger, cookie_helper
from resources.lib.cBFScrape import cBFScrape
from resources.lib.cCFScrape import cCFScrape
from resources.lib.config import cConfig


class cRequestHandler:
    def __init__(self, sUrl, caching=True, ignoreErrors=False, compression=True):
        self.__sUrl = sUrl
        self.__sRealUrl = ''
        self.__cType = 0
        self.__aParameters = {}
        self.__headerEntries = {}
        self.__cachePath = ''
        self._cookiePath = ''
        self.ignoreDiscard(False)
        self.ignoreExpired(False)
        self.caching = caching
        self.ignoreErrors = ignoreErrors
        self.compression = compression
        self.cacheTime = int(cConfig().getSetting('cacheTime', 600))
        self.requestTimeout = int(cConfig().getSetting('requestTimeout', 60))
        self.removeBreakLines(True)
        self.removeNewLines(True)
        self.__setDefaultHeader()
        self.setCachePath()
        self.__setCookiePath()
        self.__sResponseHeader = ''

        if self.requestTimeout >= 60 or self.requestTimeout <= 10:
            self.requestTimeout = 60

    def removeNewLines(self, bRemoveNewLines):
        self.__bRemoveNewLines = bRemoveNewLines

    def removeBreakLines(self, bRemoveBreakLines):
        self.__bRemoveBreakLines = bRemoveBreakLines

    def setRequestType(self, cType):
        self.__cType = cType

    def addHeaderEntry(self, sHeaderKey, sHeaderValue):
        self.__headerEntries[sHeaderKey] = sHeaderValue

    def getHeaderEntry(self, sHeaderKey):
        if sHeaderKey in self.__headerEntries:
            return self.__headerEntries[sHeaderKey]

    def addParameters(self, key, value, quote=False):
        if not quote:
            self.__aParameters[key] = value
        else:
            self.__aParameters[key] = urllib.quote(str(value))

    def getResponseHeader(self):
        return self.__sResponseHeader

    # url after redirects
    def getRealUrl(self):
        return self.__sRealUrl

    def request(self):
        self.__sUrl = self.__sUrl.replace(' ', '+')
        return self.__callRequest()

    def getRequestUri(self):
        return self.__sUrl + '?' + urllib.urlencode(self.__aParameters)

    def __setDefaultHeader(self):
        self.addHeaderEntry('User-Agent', common.FF_USER_AGENT)
        self.addHeaderEntry('Accept-Language', 'de-de,de;q=0.8,en-us;q=0.5,en;q=0.3')
        self.addHeaderEntry('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')
        if self.compression:
            self.addHeaderEntry('Accept-Encoding', 'gzip')

    def __callRequest(self):
        if self.caching and self.cacheTime > 0:
            sContent = self.readCache(self.getRequestUri())
            if sContent:
                return sContent

        cookieJar = mechanize.LWPCookieJar(filename=self._cookiePath)
        try:  # TODO ohne try evtl.
            cookieJar.load(ignore_discard=self.__bIgnoreDiscard, ignore_expires=self.__bIgnoreExpired)
        except Exception as e:
            logger.info(e)

        sParameters = urllib.urlencode(self.__aParameters, True)

        handlers = [mechanize.HTTPCookieProcessor(cookiejar=cookieJar),
                    mechanize.HTTPEquivProcessor,
                    mechanize.HTTPRefreshProcessor]
        if sys.version_info >= (2, 7, 9) and sys.version_info < (2, 7, 11):
            handlers.append(newHTTPSHandler)
        opener = mechanize.build_opener(*handlers)
        if (len(sParameters) > 0):
            oRequest = mechanize.Request(self.__sUrl, sParameters)
        else:
            oRequest = mechanize.Request(self.__sUrl)

        for key, value in self.__headerEntries.items():
            oRequest.add_header(key, value)
        cookieJar.add_cookie_header(oRequest)

        user_agent = self.__headerEntries.get('User-Agent', common.FF_USER_AGENT)

        try:
            oResponse = opener.open(oRequest, timeout=self.requestTimeout)
        except mechanize.HTTPError, e:
            if e.code == 503 and e.headers.get("Server") == 'cloudflare-nginx':
                html = e.read()
                oResponse = self.__check_protection(html, user_agent, cookieJar)
                if not oResponse:
                    logger.error("Failed to get CF-Cookie for Url: " + self.__sUrl)
                    return ''
            elif not self.ignoreErrors:
                xbmcgui.Dialog().ok('xStream', 'Fehler beim Abrufen der Url:', self.__sUrl, str(e))
                logger.error("HTTPError " + str(e) + " Url: " + self.__sUrl)
                return ''
            else:
                oResponse = e
        except mechanize.URLError, e:
            if not self.ignoreErrors:
                if hasattr(e.reason, 'args') and e.reason.args[0] == 1 and sys.version_info < (2, 7, 9):
                    xbmcgui.Dialog().ok('xStream', str(e.reason), '','For this request is Python v2.7.9 or higher required.')
                else:
                    xbmcgui.Dialog().ok('xStream', str(e.reason))
            logger.error("URLError " + str(e.reason) + " Url: " + self.__sUrl)
            return ''
        except httplib.HTTPException, e:
            if not self.ignoreErrors:
                xbmcgui.Dialog().ok('xStream', str(e))
            logger.error("HTTPException " + str(e) + " Url: " + self.__sUrl)
            return ''

        sContent = oResponse.read()

        checked_response = self.__check_protection(sContent, user_agent, cookieJar)
        if checked_response:
            oResponse = checked_response
            sContent = oResponse.read()

        cookie_helper.check_cookies(cookieJar)
        cookieJar.save(ignore_discard=self.__bIgnoreDiscard, ignore_expires=self.__bIgnoreExpired)

        self.__sResponseHeader = oResponse.info()
        # handle gzipped content
        if self.__sResponseHeader.get('Content-Encoding') == 'gzip':
            import gzip
            import StringIO
            data = StringIO.StringIO(sContent)
            gzipper = gzip.GzipFile(fileobj=data, mode='rb')
            try:
                sContent = gzipper.read()
            except:
                sContent = gzipper.extrabuf

        if (self.__bRemoveNewLines == True):
            sContent = sContent.replace("\n", "")
            sContent = sContent.replace("\r\t", "")

        if (self.__bRemoveBreakLines == True):
            sContent = sContent.replace("&nbsp;", "")

        self.__sRealUrl = oResponse.geturl()

        oResponse.close()
        if self.caching and self.cacheTime > 0:
            self.writeCache(self.getRequestUri(), sContent)

        return sContent

    def __check_protection(self, html, user_agent, cookie_jar):
        oResponse = None

        if 'cf-browser-verification' in html:
            oResponse = cCFScrape().resolve(self.__sUrl, cookie_jar, user_agent)
        elif 'Blazingfast.io' in html:
            oResponse = cBFScrape().resolve(self.__sUrl, cookie_jar, user_agent)

        return oResponse

    def getHeaderLocationUrl(self):
        opened = mechanize.urlopen(self.__sUrl)
        return opened.geturl()

    def __setCookiePath(self):
        profilePath = common.profilePath
        cookieFile = os.path.join(profilePath, 'cookies.txt')
        if not os.path.exists(cookieFile):
            file = open(cookieFile, 'w')
            file.close()
        self._cookiePath = cookieFile

    def getCookie(self, sCookieName, sDomain=''):
        cookieJar = mechanize.LWPCookieJar()
        try:  # TODO ohne try evtl.
            cookieJar.load(self._cookiePath, self.__bIgnoreDiscard, self.__bIgnoreExpired)
        except Exception as e:
            logger.info(e)

        for entry in cookieJar:
            if entry.name == sCookieName:
                if sDomain == '':
                    return entry
                elif entry.domain == sDomain:
                    return entry

        return False

    def setCookie(self, oCookie):
        cookieJar = mechanize.LWPCookieJar()
        try:  # TODO ohne try evtl.
            cookieJar.load(self._cookiePath, self.__bIgnoreDiscard, self.__bIgnoreExpired)
        except Exception as e:
            logger.info(e)

        cookieJar.set_cookie(oCookie)

        cookieJar.save(self._cookiePath, self.__bIgnoreDiscard, self.__bIgnoreExpired)

    def ignoreDiscard(self, bIgnoreDiscard):
        self.__bIgnoreDiscard = bIgnoreDiscard

    def ignoreExpired(self, bIgnoreExpired):
        self.__bIgnoreExpired = bIgnoreExpired

    ###Caching
    def setCachePath(self, cache=''):
        if not cache:
            profilePath = common.profilePath
            cache = os.path.join(profilePath, 'htmlcache')
        if not os.path.exists(cache):
            os.makedirs(cache)
        self.__cachePath = cache

    def readCache(self, url):
        h = hashlib.md5(url).hexdigest()
        cacheFile = os.path.join(self.__cachePath, h)
        fileAge = self.getFileAge(cacheFile)
        if fileAge > 0 and fileAge < self.cacheTime:
            try:
                fhdl = file(cacheFile, 'r')
                content = fhdl.read()
            except:
                logger.info('Could not read Cache')
            if content:
                logger.info('read html for %s from cache' % url)
                return content
        return ''

    def writeCache(self, url, content):
        h = hashlib.md5(url).hexdigest()
        cacheFile = os.path.join(self.__cachePath, h)
        try:
            fhdl = file(cacheFile, 'w')
            fhdl.write(content)
        except:
            logger.info('Could not write Cache')

    def getFileAge(self, cacheFile):
        try:
            fileAge = time.time() - os.stat(cacheFile).st_mtime
        except:
            return 0
        return fileAge

    def clearCache(self):
        files = os.listdir(self.__cachePath)
        for file in files:
            cacheFile = os.path.join(self.__cachePath, file)
            fileAge = self.getFileAge(cacheFile)
            if fileAge > self.cacheTime:
                os.remove(cacheFile)


# python 2.7.9 and 2.7.10 certificate workaround
class newHTTPSHandler(mechanize.HTTPSHandler):
    def do_open(self, conn_factory, req):
        conn_factory = newHTTPSConnection
        return mechanize.HTTPSHandler.do_open(self, conn_factory, req)


class newHTTPSConnection(httplib.HTTPSConnection):
    def __init__(self, host, port=None, key_file=None, cert_file=None, strict=None,
                 timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, context=None):
        import ssl
        context = ssl._create_unverified_context()
        httplib.HTTPSConnection.__init__(self, host, port, key_file, cert_file, strict, timeout, source_address,
                                         context)
