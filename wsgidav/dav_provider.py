"""
dav_provider
============

:Author: Martin Wendt, moogle(at)wwwendt.de 
:Author: Ho Chun Wei, fuzzybr80(at)gmail.com (author of original PyFileServer)
:Copyright: Lesser GNU Public License, see LICENSE file attached with package

This module implements DAVResource and DAVProvider objects.

DAVResource
-----------
Represents an existing (i.e. mapped) WebDAV resource or collection.
A DAVResource object is created by a call the DAVProvider::

    res = provider.getResourceInst(path)
    if res is None:
        raise DAVError(HTTP_NOT_FOUND)

The resource then may be used to query different attributes like ``res.name``,
``res.isCollection``, ``res.getContentLength()``, and ``res.supportEtag()``. 

It also implements operations, that require an *existing* resource, like:
``getPreferredPath()``, ``createCollection()``, or ``getPropertyValue()``.

Usage::

    res = provider.getResourceInst(path)
    if res is not None:
        print res.getName()


DAVProvider
-----------
A DAV provider represents a shared WebDAV system.

There is only one provider instance per share, which is created during 
server start-up. After that, the dispatcher (``request_resolver.RequestResolver``) 
parses the request URL and adds it to the WSGI environment, so it 
can be accessed like this::

    provider = environ["wsgidav.provider"]

The main purpose of the provider is to create DAVResource objects for URLs.



Abstract base class for DAV resource providers.

This module serves these purposes:

  1. Documentation of the DAVProvider interface
  2. Common base class for all DAV providers
  3. Default implementation for most functionality that a resource provider must
     deliver.

If no default implementation can be provided, then all write actions generate
FORBIDDEN errors. Read requests generate NOT_IMPLEMENTED errors.
 
See DEVELOPERS.txt_ for more information about the WsgiDAV architecture.

.. _DEVELOPERS.txt: http://wiki.wsgidav-dev.googlecode.com/hg/DEVELOPERS.html  

Supporting Objects
------------------

The DAVProvider takes two supporting objects:   
   
propertyManager
   An object that provides storage for dead properties assigned for webDAV resources.
   
   PropertyManagers must provide the methods as described in 
   ``wsgidav.interfaces.propertymanagerinterface``

   See property_manager.PropertyManager for a sample implementation
   using shelve.

lockmMnager
   An object that provides storage for locks made on webDAV resources.
   
   LockManagers must provide the methods as described in 
   ``wsgidav.interfaces.lockmanagerinterface``

   See lock_manager.LockManager for a sample implementation
   using shelve.
"""
import sys
import time
import traceback
import urllib
from wsgidav import util
# Trick PyDev to do intellisense and don't produce warnings:
from util import etree #@UnusedImport
if False: from xml.etree import ElementTree as etree     #@Reimport @UnresolvedImport

from dav_error import DAVError, \
    HTTP_NOT_FOUND, HTTP_FORBIDDEN,\
    PRECONDITION_CODE_ProtectedProperty, asDAVError

__docformat__ = "reStructuredText"

_logger = util.getModuleLogger(__name__)

_standardLivePropNames = ["{DAV:}creationdate", 
                          "{DAV:}displayname", 
                          "{DAV:}getcontenttype",
                          "{DAV:}resourcetype",
                          "{DAV:}getlastmodified", 
                          "{DAV:}getcontentlength", 
                          "{DAV:}getetag", 
                          "{DAV:}getcontentlanguage", 
#                          "{DAV:}source", # removed in rfc4918
                          ] 
_lockPropertyNames = ["{DAV:}lockdiscovery", 
                      "{DAV:}supportedlock"]

DAVHRES_Continue = "continue"
DAVHRES_Done = "done"

#===============================================================================
# DAVResource 
#===============================================================================
class DAVResource(object):
    """Represents a single existing DAV resource instance.

    Instances of this class are created through the DAVProvider::
        
        res = provider.getResourceInst(path)
        if res and res.isCollection:
            print res.getDisplayName()
            
    In the example above res will be ``None``, if the path cannot be mapped to
    an existing resource.
    The following attributes and methods are considered 'cheap':: 
    
        res.path
        res.provider
        res.name
        res.isCollection
    
    Querying other attributes is considered 'expensive' and may be delayed until 
    the first access. 

        getContentLength()
        getContentType()
        getCreationDate()
        getDisplayName()
        getEtag()
        getLastModified()
        supportRanges()
        
        supportEtag()
        supportModified()
        supportContentLength()
    
    These functions return ``None``, if the property is not available, or
    not supported.   
    
    Note, that custom DAVProviders may choose different implementations and
    return custom DAVResource objects, that do not use ``self._init()`` to
    cache properties. Only make sure, that your DAVResource object implements 
    the getters.  

    See also DAVProvider.getResourceInst().
    """
    def __init__(self, provider, path, isCollection):
        assert path=="" or path.startswith("/")
        self.provider = provider
        self.path = path
        self.isCollection = isCollection
        self.name = util.getUriName(self.path)
    
    
    def __repr__(self):
#        d = getattr(self, "_customLiveProps", None)
        return "%s(%s): %s" % (self.__class__.__name__, self.isCollection, self.path)


    def getContentLanguage(self):
        """Contains the Content-Language header returned by a GET without accept 
        headers.
        
        The getcontentlanguage property MUST be defined on any DAV compliant 
        resource that returns the Content-Language header on a GET.
        """
        return None
    def getContentLength(self):
        """Contains the Content-Length header returned by a GET without accept 
        headers.
        
        The getcontentlength property MUST be defined on any DAV compliant 
        resource that returns the Content-Length header in response to a GET.
        """
        return None
    def getContentType(self):
        """Contains the Content-Type header returned by a GET without accept 
        headers.
        
        This getcontenttype property MUST be defined on any DAV compliant 
        resource that returns the Content-Type header in response to a GET.
        See http://www.webdav.org/specs/rfc4918.html#PROPERTY_getcontenttype
        """
        raise NotImplementedError()
    def getCreationDate(self):
        """Records the time and date the resource was created.
        
        The creationdate property should be defined on all DAV compliant 
        resources. If present, it contains a timestamp of the moment when the 
        resource was created (i.e., the moment it had non-null state).
        """
        return None
    def getDisplayName(self):
        """Provides a name for the resource that is suitable for presentation to 
        a user.
        
        The displayname property should be defined on all DAV compliant 
        resources. If present, the property contains a description of the 
        resource that is suitable for presentation to a user.
        
        """
        return None
    def getEtag(self):
        """
        See http://www.webdav.org/specs/rfc4918.html#PROPERTY_getetag
        """
        return None
    def getLastModified(self):
        """Contains the Last-Modified header returned by a GET method without 
        accept headers.

        Return None, if this live property is not supported.

        Note that the last-modified date on a resource may reflect changes in 
        any part of the state of the resource, not necessarily just a change to 
        the response to the GET method. For example, a change in a property may 
        cause the last-modified date to change. The getlastmodified property 
        MUST be defined on any DAV compliant resource that returns the 
        Last-Modified header in response to a GET.
        """
        return None
    
    def displayType(self):
        # TODO: remove this method
        if self.isCollection:
            return "Collection"
        return "Non-Collection"

#    def getDirectoryInfo(self):
#        return False
    
    def supportRanges(self):
        """Return True, if this non-resource supports Range on GET requests.

        This method is only called for non-resources.
        """
        raise NotImplementedError()

    def supportContentLength(self):
        return self.getContentLength() is not None
    def supportEtag(self):
        return self.getEtag() is not None
    def supportModified(self):
        return self.getLastModified() is not None
    
#    def supportNativeCopy(self):
#        """Return True, if provider handles copying by itself."""
#        return False
#    def supportNativeDelete(self):
#        """Return True, if provider handles deleting by itself."""
#        return False
#    def supportNativeMove(self):
#        """Return True, if provider handles moving by itself."""
#        return False
    
    
    def getPreferredPath(self):
        """Return preferred mapping for a resource mapping.
        
        Different URLs may map to the same resource, e.g.:
            '/a/b' == '/A/b' == '/a/b/'
        getPreferredPath() returns the same value for all these variants, e.g.:
            '/a/b/'   (assuming resource names considered case insensitive)

        @param path: a UTF-8 encoded, unquoted byte string.
        @return: a UTF-8 encoded, unquoted byte string.
        """
        if self.path in ("", "/"):
            return "/"
        # Append '/' for collections
        if self.isCollection and not self.path.endswith("/"):
            return self.path + "/"
        # TODO: handle case-sensitivity, depending on OS 
        # (FileSystemProvider could do this with os.path:
        # (?) on unix we can assume that the path already matches exactly the case of filepath
        #     on windows we could use path.lower() or get the real case from the file system
        return self.path

    
    def getRefUrl(self):
        """Return the quoted, absolute, unique URL of a resource, relative to appRoot.
        
        Byte string, UTF-8 encoded, quoted.
        Starts with a '/'. Collections also have a trailing '/'.
        
        This is basically the same as getPreferredPath, but deals with 
        'virtual locations' as well.
        
        e.g. '/a/b' == '/A/b' == '/bykey/123' == '/byguid/abc532'
        
        getRefUrl() returns the same value for all these URLs, so it can be
        used as a key for locking and persistence storage. 

        DAV providers that allow virtual-mappings must override this method.

        See also comments in DEVELOPERS.txt glossary.
        """
        return urllib.quote(self.provider.sharePath + self.getPreferredPath())

    
#    def getRefKey(self):
#        """Return an unambigous identifier string for a resource.
#        
#        Since it is always unique for one resource, <refKey> is used as key for 
#        the lock- and property storage dictionaries.
#        
#        This default implementation calls getRefUrl(), and strips a possible 
#        trailing '/'.
#        """
#        refKey = self.getRefUrl(path)
#        if refKey == "/":
#            return refKey
#        return refKey.rstrip("/")

    
    def getHref(self):
        """Convert path to a URL that can be passed to XML responses.
        
        Byte string, UTF-8 encoded, quoted.

        See http://www.webdav.org/specs/rfc4918.html#rfc.section.8.3
        We are using the path-absolute option. i.e. starting with '/'. 
        URI ; See section 3.2.1 of [RFC2068]
        """
        # Nautilus chokes, if href encodes '(' as '%28'
        # So we don't encode 'extra' and 'safe' characters (see rfc2068 3.2.1)
        safe = "/" + "!*'()," + "$-_|."
        return urllib.quote(self.provider.mountPath + self.provider.sharePath 
                            + self.getPreferredPath(), safe=safe)


#    def getParent(self):
#        """Return parent DAVResource or None.
#        
#        There is NO checking, if the parent is really a mapped collection.
#        """
#        parentpath = util.getUriParent(self.path)
#        if not parentpath:
#            return None
#        return self.provider.getResourceInst(parentpath)


    def getMemberNames(self):
        """Return list of (direct) collection member names (UTF-8 byte strings).
        
        Every provider MUST override this method.
        """
        raise NotImplementedError()


    def getDescendants(self, collections=True, resources=True, 
                       depthFirst=False, depth="infinity", addSelf=False):
        """Return a list DAVResource objects of a collection (children, grand-children, ...).

        This default implementation calls getMemberNames() and 
        provider.getResourceInst() recursively.
        
        :Parameters:
            depthFirst : bool
                use <False>, to list containers before content.
                (e.g. when moving / copying branches.)
                Use <True>, to list content before containers. 
                (e.g. when deleting branches.)
            depth : string
                '0' | '1' | 'infinity'
        """
        assert depth in ("0", "1", "infinity")
        res = []
        if addSelf and not depthFirst:
            res.append(self)
        if depth != "0" and self.isCollection:
            pathPrefix = self.path.rstrip("/") + "/"
            for name in self.getMemberNames():
                child = self.provider.getResourceInst(pathPrefix + name)
                assert child, "Could not read resource inst '%s'" % (pathPrefix + name)
                want = (collections and child.isCollection) or (resources and not child.isCollection)
                if want and not depthFirst: 
                    res.append(child)
                if child.isCollection and depth == "infinity":
                    res.extend(child.getDescendants(collections, resources, depthFirst, depth, addSelf=False))
                if want and depthFirst: 
                    res.append(child)
        if addSelf and depthFirst:
            res.append(self)
        return res

        
    def getDirInfo(self):
        """Return list of dictionaries describing direct collection members.
        
        This method is called by dir_browser middleware, and may be used to
        provide the directory listing info in a efficient way.
        """
        assert self.isCollection
        raise NotImplementedError()


    # --- Properties -----------------------------------------------------------
     
    def getPropertyNames(self, isAllProp):
        """Return list of supported property names in Clark Notation.
        
        Note that 'allprop', despite its name, which remains for 
        backward-compatibility, does not return every property, but only dead 
        properties and the live properties defined in RFC4918.
        
        This default implementation returns a combination of:
        
        - Supported standard live properties in the {DAV:} namespace, if the 
          using related getter method returns not None.
        - {DAV:}lockdiscovery and {DAV:}supportedlock, if a lock manager is 
          present
        - If a property manager is present, then a list of dead properties is 
          appended
        
        A resource provider may override this method, to add a list of 
        supported custom live property names. 
        """
        ## Live properties
        propNameList = []
        
        propNameList.append("{DAV:}resourcetype")
        
        if self.getCreationDate() is not None:
            propNameList.append("{DAV:}creationdate")
        if self.getContentLength() is not None:
            assert not self.isCollection
            propNameList.append("{DAV:}getcontentlength")
        if self.getContentType() is not None:
            propNameList.append("{DAV:}getcontenttype")
        if self.getLastModified() is not None:
            propNameList.append("{DAV:}getlastmodified")
        if self.getDisplayName() is not None:
            propNameList.append("{DAV:}displayname")
        if self.getEtag() is not None:
            propNameList.append("{DAV:}getetag")
            
        ## Locking properties 
        if self.provider.lockManager and not self.preventLocking():
            propNameList.extend(_lockPropertyNames)

        ## Dead properties
        if self.provider.propManager:
            refUrl = self.getRefUrl()
            propNameList.extend(self.provider.propManager.getProperties(refUrl))
                
        return propNameList


    def getProperties(self, mode, nameList=None):
        """Return properties as list of 2-tuples (name, value).

        If mode is 'propname', then None is returned for the value.
        
        name 
            the property name in Clark notation.
        value 
            may have different types, depending on the status: 
            - string or unicode: for standard property values.
            - etree.Element: for complex values.
            - DAVError in case of errors.
            - None: if mode == 'propname'.

        @param mode: "allprop", "propname", or "named"
        @param nameList: list of property names in Clark Notation (required for mode 'named')
        
        This default implementation basically calls self.getPropertyNames() to 
        get the list of names, then call self.getPropertyValue on each of them.
        """
        assert mode in ("allprop", "propname", "named")

        if mode in ("allprop", "propname"):
            # TODO: 'allprop' could have nameList, when <include> option is 
            # implemented
            assert nameList is None
            nameList = self.getPropertyNames(mode == "allprop")
        else:
            assert nameList is not None

        propList = []
        namesOnly = (mode == "propname")
        for name in nameList:
            try:
                if namesOnly:
                    propList.append( (name, None) )
                else:
                    value = self.getPropertyValue(name)
                    propList.append( (name, value) )
            except DAVError, e:
                propList.append( (name, e) )
            except Exception, e:
                propList.append( (name, asDAVError(e)) )
                if self.provider.verbose >= 2:
                    traceback.print_exc(10, sys.stderr)  
                    
        return propList


    def getPropertyValue(self, propname):
        """Return the value of a property.
        
        propname:
            the property name in Clark notation.
        return value:
            may have different types, depending on the status:
             
            - string or unicode: for standard property values.
            - lxml.etree.Element: for complex values.
            
            If the property is not available, a DAVError is raised.
            
        This default implementation handles ``{DAV:}lockdiscovery`` and
        ``{DAV:}supportedlock`` using the associated lock manager.
        
        All other *live* properties (i.e. propname starts with ``{DAV:}``) are 
        delegated to the self.xxx() getters.
        
        Finally, other properties are considered *dead*, and are handled  by 
        the associated property manager. 
        """
        refUrl = self.getRefUrl()

        # lock properties
        lm = self.provider.lockManager     
        if lm and propname == "{DAV:}lockdiscovery":
            # TODO: we return HTTP_NOT_FOUND if no lockmanager is present. Correct?
            activelocklist = lm.getUrlLockList(refUrl)
            lockdiscoveryEL = etree.Element(propname)
            for lock in activelocklist:
                activelockEL = etree.SubElement(lockdiscoveryEL, "{DAV:}activelock")

                locktypeEL = etree.SubElement(activelockEL, "{DAV:}locktype")
                etree.SubElement(locktypeEL, "{DAV:}%s" % lock["type"])

                lockscopeEL = etree.SubElement(activelockEL, "{DAV:}lockscope")
                etree.SubElement(lockscopeEL, "{DAV:}%s" % lock["scope"])
                
                etree.SubElement(activelockEL, "{DAV:}depth").text = lock["depth"]
                # lock["owner"] is an XML string
                ownerEL = util.stringToXML(lock["owner"])

                activelockEL.append(ownerEL)
                
                timeout = lock["timeout"]
                if timeout < 0:
                    timeout =  "Infinite"
                else:
                    timeout = "Second-" + str(long(timeout - time.time())) 
                etree.SubElement(activelockEL, "{DAV:}timeout").text = timeout
                
                locktokenEL = etree.SubElement(activelockEL, "{DAV:}locktoken")
                etree.SubElement(locktokenEL, "{DAV:}href").text = lock["token"]

                # TODO: this is ugly: 
                #       res.getPropertyValue("{DAV:}lockdiscovery")
                #       
#                lockRoot = self.getHref(self.provider.refUrlToPath(lock["root"]))
                lockPath = self.provider.refUrlToPath(lock["root"])
                lockRes = self.provider.getResourceInst(lockPath)
                lockHref = lockRes.getHref()
#                print "lockedRoot: %s -> href=%s" % (lockPath, lockHref)

                lockrootEL = etree.SubElement(activelockEL, "{DAV:}lockroot")
                etree.SubElement(lockrootEL, "{DAV:}href").text = lockHref

            return lockdiscoveryEL            

        elif lm and propname == "{DAV:}supportedlock":
            # TODO: we return HTTP_NOT_FOUND if no lockmanager is present. Correct?
            # TODO: the lockmanager should decide about it's features
            supportedlockEL = etree.Element(propname)

            lockentryEL = etree.SubElement(supportedlockEL, "{DAV:}lockentry")
            lockscopeEL = etree.SubElement(lockentryEL, "{DAV:}lockscope")
            etree.SubElement(lockscopeEL, "{DAV:}exclusive")
            locktypeEL = etree.SubElement(lockentryEL, "{DAV:}locktype")
            etree.SubElement(locktypeEL, "{DAV:}write")

            lockentryEL = etree.SubElement(supportedlockEL, "{DAV:}lockentry")
            lockscopeEL = etree.SubElement(lockentryEL, "{DAV:}lockscope")
            etree.SubElement(lockscopeEL, "{DAV:}shared")
            locktypeEL = etree.SubElement(lockentryEL, "{DAV:}locktype")
            etree.SubElement(locktypeEL, "{DAV:}write")
            
            return supportedlockEL

        elif propname.startswith("{DAV:}"):
            # Standard live property (raises HTTP_NOT_FOUND if not supported)
            if propname == "{DAV:}creationdate" and self.getCreationDate() is not None:
                # Note: uses RFC3339 format (ISO 8601)
                return util.getRfc3339Time(self.getCreationDate())
            elif propname == "{DAV:}getcontenttype" and self.getContentType() is not None:
                return self.getContentType()
            elif propname == "{DAV:}resourcetype":
                if self.isCollection:
                    resourcetypeEL = etree.Element(propname)
                    etree.SubElement(resourcetypeEL, "{DAV:}collection")
                    return resourcetypeEL            
                return ""   
            elif propname == "{DAV:}getlastmodified" and self.getLastModified() is not None:
                # Note: uses RFC1123 format
                return util.getRfc1123Time(self.getLastModified())
            elif propname == "{DAV:}getcontentlength" and self.getContentLength() is not None:
                # Note: must be a numeric string
                return str(self.getContentLength())
            elif propname == "{DAV:}getetag" and self.getEtag() is not None:
                return self.getEtag()
            elif propname == "{DAV:}displayname" and self.getDisplayName() is not None:
                return self.getDisplayName()
    
            # Unsupported No persistence available, or property not found
            raise DAVError(HTTP_NOT_FOUND)               
        
        # Dead property
        pm = self.provider.propManager
        if pm:
            value = pm.getProperty(refUrl, propname)
            if value is not None:
#                return etree.XML(value)
                return util.stringToXML(value) 

        # No persistence available, or property not found
        raise DAVError(HTTP_NOT_FOUND)               
    

    def setPropertyValue(self, propname, value, dryRun=False):
        """Set a property value or remove a property.
        
        value == None means 'remove property'.
        Raise HTTP_FORBIDDEN if property is read-only, or not supported.

        When dryRun is True, this function should raise errors, as in a real
        run, but MUST NOT change any data.
                 
        This default implementation 
        
        - raises HTTP_FORBIDDEN, if trying to modify a locking property
        - raises HTTP_FORBIDDEN, if trying to modify a {DAV:} property 
        - stores everything else as dead property, if a property manager is 
          present.
        - raises HTTP_FORBIDDEN, else 
        
        Removing a non-existing prop is NOT an error. 
        
        Note: RFC 4918 states that {DAV:}displayname 'SHOULD NOT be protected' 

        A resource provider may override this method, to update supported custom 
        live properties. 
        """
        assert value is None or isinstance(value, (etree._Element))

        if propname in _lockPropertyNames:
            # Locking properties are always read-only
            raise DAVError(HTTP_FORBIDDEN,  
                           preconditionCode=PRECONDITION_CODE_ProtectedProperty)  

        # Dead property
        pm = self.provider.propManager
        if pm and not propname.startswith("{DAV:}"):
            refUrl = self.getRefUrl()
            if value is None:
                return pm.removeProperty(refUrl, propname)
            else:
                value = etree.tostring(value)
                return pm.writeProperty(refUrl, propname, value, dryRun)             

        raise DAVError(HTTP_FORBIDDEN) 




    def removeAllProperties(self, recursive):
        """Remove all associated dead properties."""
        if self.provider.propManager:
            self.provider.propManager.removeProperties(self.getRefUrl())




    # --- Locking --------------------------------------------------------------

    def preventLocking(self):
        """Return True, to prevent locking.
        
        This default implementation returns ``False``, so standard processing
        takes place: locking (and refreshing of locks) is implemented using
        the lock manager, if one is configured. 
        """
        return False               

    
    def isLocked(self):
        """Return True, if URI is locked."""
        if self.provider.lockManager is None:
            return False
        return self.provider.lockManager.isUrlLocked(self.getRefUrl())


    def removeAllLocks(self, recursive):
        if self.provider.lockManager:
            self.provider.lockManager.removeAllLocksFromUrl(self.getRefUrl())


    # --- Read / write ---------------------------------------------------------
    
    def createEmptyResource(self, name):
        """Create and return an empty (length-0) resource as member of self.
        
        Called for LOCK requests on unmapped URLs.
        
        Preconditions (to be ensured by caller):
        
          - this must be a collection
          - <self.path + name> must not exist  
          - there must be no conflicting locks

        Returns a DAVResuource.
        
        This method MUST be implemented by all providers that support write 
        access.
        This default implementation simply raises HTTP_FORBIDDEN.
        """
        assert self.isCollection
        raise DAVError(HTTP_FORBIDDEN)               
    

    def createCollection(self, name):
        """Create a new collection as member of self.
        
        Preconditions (to be ensured by caller):
        
          - this must be a collection
          - <self.path + name> must not exist  
          - there must be no conflicting locks

        This method MUST be implemented by all providers that support write 
        access.
        This default implementation raises HTTP_FORBIDDEN.
        """
        assert self.isCollection
        raise DAVError(HTTP_FORBIDDEN)               


    def getContent(self):
        """Open content as a stream for reading.

        Returns a file-like object / stream containing the contents of the
        resource specified.
        The application will close() the stream.      
         
        This method MUST be implemented by all providers.
        """
        assert not self.isCollection
        raise NotImplementedError()
    

    # TODO: rename to beginWrite() and add endWrite(success)
    def openResourceForWrite(self, contentType=None):
        """Open content as a stream for writing.
         
        This method MUST be implemented by all providers that support write 
        access.
        """
        assert self.isResource()
        raise DAVError(HTTP_FORBIDDEN)               

    
    def handleDelete(self):
        """Handle a DELETE request natively.
        
        This method is called by the DELETE handler after checking for valid
        request syntax and making sure that there are no conflicting locks and
        If-headers.         
        Depending on the return value, this provider can control further 
        processing:
        
        False:
            handleDelete() did not do anything. WsgiDAV will process the request
            by calling delete() for every resource, bottom-up.
        True:
            handleDelete() has successfully performed the DELETE request.
            HTTP_NO_CONTENT will be reported to the DAV client.
        List of errors:
            handleDelete() tried to perform the delete request, but failed
            completely or partially. A list of errors is returned like
            ``[ (<ref-url>, <DAVError>), ... ]``
            These errors will be reported to the client.
        DAVError raised:
            handleDelete() refuses to perform the delete request. The DAVError 
            will be reported to the client.

        An implementation may choose to apply other semantics and return True.
        For example deleting '/by_tag/cool/myres' may simply remove the 'cool' 
        tag from 'my_res'. 
        In this case, the resource might still be available by other URLs, so 
        locks and properties are not removed.

        This default implementation returns ``False``, so standard processing
        takes place.
         
        Implementation of this method is OPTIONAL.
        """
        return False               

    
    def supportRecursiveDelete(self):
        """Return True, if delete() may be called on non-empty collections 
        (see comments there)."""
        return False

    
    def delete(self):
        """Remove this resource (recursive).
        
        Preconditions (to be ensured by caller):
        
          - there are no conflicting locks or If-headers
          - if supportRecursiveDelete() is False, and this is a collection,
            all members have already been deleted.

        When supportRecursiveDelete is True, this method must be prepared to 
        handle recursive deletes. This implies that child errors must be 
        reported as tuple list [ (<ref-url>, <DAVError>), ... ].
        See http://www.webdav.org/specs/rfc4918.html#delete-collections

        This function
        
          - removes this resource
          - if this is a non-empty collection, also removes all members.
            Note that this may only occur, if supportRecursiveDelete is True.
          - For recursive deletes, return a list of error tuples for all failed 
            resource paths.
          - removes associated locks
          - removes associated dead properties
          - raises HTTP_FORBIDDEN for read-only resources
          - raises HTTP_INTERNAL_ERROR on error
        
        This method MUST be implemented by all providers that support write 
        access.
        """
        raise DAVError(HTTP_FORBIDDEN)               

    
    def handleCopy(self, destPath, depthInfinity):
        """Handle a COPY request natively.
        
        This method is called by the COPY handler after checking for valid
        request syntax and making sure that there are no conflicting locks and
        If-headers.         
        Depending on the return value, this provider can control further 
        processing:
        
        False:
            handleCopy() did not do anything. WsgiDAV will process the request
            by calling copyMoveSingle() for every resource, bottom-up.
        True:
            handleCopy() has successfully performed the COPY request.
            HTTP_NO_CONTENT/HTTP_CREATED will be reported to the DAV client.
        List of errors:
            handleCopy() tried to perform the copy request, but failed
            completely or partially. A list of errors is returned like
            ``[ (<ref-url>, <DAVError>), ... ]``
            These errors will be reported to the client.
        DAVError raised:
            handleCopy() refuses to perform the copy request. The DAVError 
            will be reported to the client.

        An implementation may choose to apply other semantics and return True.
        For example copying '/by_tag/cool/myres' to '/by_tag/hot/myres' may 
        simply add a 'hot' tag. 
        In this case, the resource might still be available by other URLs, so 
        locks and properties are not removed.

        This default implementation returns ``False``, so standard processing
        takes place.
         
        Implementation of this method is OPTIONAL.
        """
        return False               

    
    def copyMoveSingle(self, destPath, isMove):
        """Copy or move this resource to destPath (non-recursive).
        
        Preconditions (to be ensured by caller):
        
          - there must not be any conflicting locks on destination
          - overwriting is only allowed (i.e. destPath exists), when source and 
            dest are of the same type ((non-)collections) and a Overwrite='T' 
            was passed 
          - destPath must not be a child path of this resource

        This function
        
          - Overwrites non-collections content, if destination exists.
          - MUST NOT copy collection members.
          - MUST NOT copy locks
          - SHOULD copy live properties, when appropriate.
            E.g. displayname should be copied, but creationdate should be
            reset if the target did not exist before.
            See http://www.webdav.org/specs/rfc4918.html#dav.properties
          - SHOULD copy dead properties
          - raises HTTP_FORBIDDEN for read-only providers
          - raises HTTP_INTERNAL_ERROR on error

        When isMove is True,
        
          - Live properties should be moved too (e.g. creationdate)
          - Non-collections must be moved, not copied
          - For collections, this function behaves like in copy-mode: 
            detination collection must be created and properties are copied.
            Members are NOT created.
            The source collection MUST NOT be removed
        
        This method MUST be implemented by all providers that support write 
        access.
        """
        raise DAVError(HTTP_FORBIDDEN)               



    def handleMove(self, destPath):
        """Handle a MOVE request natively.
        
        This method is called by the MOVE handler after checking for valid
        request syntax and making sure that there are no conflicting locks and
        If-headers.         
        Depending on the return value, this provider can control further 
        processing:
        
        False:
            handleMove() did not do anything. WsgiDAV will process the request
            by calling delete() and copyMoveSingle() for every resource, bottom-up.
        True:
            handleMove() has successfully performed the MOVE request.
            HTTP_NO_CONTENT/HTTP_CREATED will be reported to the DAV client.
        List of errors:
            handleMove() tried to perform the move request, but failed
            completely or partially. A list of errors is returned like
            ``[ (<ref-url>, <DAVError>), ... ]``
            These errors will be reported to the client.
        DAVError raised:
            handleMove() refuses to perform the move request. The DAVError 
            will be reported to the client.

        An implementation may choose to apply other semantics and return True.
        For example moving '/by_tag/cool/myres' to '/by_tag/hot/myres' may 
        simply remove the 'cool' tag from 'my_res' and add a 'hot' tag instead. 
        In this case, the resource might still be available by other URLs, so 
        locks and properties are not removed.

        This default implementation returns ``False``, so standard processing
        takes place.
         
        Implementation of this method is OPTIONAL.
        """
        return False               

    
    def supportRecursiveMove(self, destPath):
        """Return True, if moveRecursive() is available (see comments there)."""
        return False

    
    def moveRecursive(self, destPath):
        """Move this resource and members to destPath.
        
        This method is only called, when supportRecursiveMove() returns True. 

        MOVE is frequently used by clients to rename a file without changing its 
        parent collection, so it's not appropriate to reset all live properties 
        that are set at resource creation. For example, the DAV:creationdate 
        property value SHOULD remain the same after a MOVE.

        Preconditions (to be ensured by caller):
        
          - there must not be any conflicting locks or If-header on source
          - there must not be any conflicting locks or If-header on destination
          - destPath must not exist 
          - destPath must not be a member of this resource

        This method must be prepared to handle recursive moves. This implies 
        that child errors must be reported as tuple list 
        [ (<ref-url>, <DAVError>), ... ].
        See http://www.webdav.org/specs/rfc4918.html#move-collections

        This function
        
          - moves this resource and all members to destPath.
          - MUST NOT move associated locks.
            Instead, if the source (or children thereof) have locks, then
            these locks should be removed.
          - SHOULD maintain associated live properties, when applicable
            See http://www.webdav.org/specs/rfc4918.html#dav.properties
          - MUST maintain associated dead properties
          - raises HTTP_FORBIDDEN for read-only resources
          - raises HTTP_INTERNAL_ERROR on error
        
        An implementation may choose to apply other semantics.
        For example copying '/by_tag/cool/myres' to '/by_tag/new/myres' may 
        simply add a 'new' tag to 'my_res'. 

        This method is only called, when self.supportRecursiveMove() returns 
        True. Otherwise, the request server implements MOVE using delete/copy.
        
        This method MAY be implemented in order to improve performance.
        """
        raise DAVError(HTTP_FORBIDDEN)               


# This would NOT work, since we would call moveSingle() top-down.
# So the source children would be deleted, before we copy them. 

#    def moveSingle(self, destPath):
#        """Move this resource to destPath (non-recursive).
#        
#        Preconditions (to be ensured by caller):
#        
#          - there must not be any conflicting locks on destination
#          - destPath does not exist
#          - destPath parent exists and is a collection
#          - destPath must not be a child path of this resource
#
#        This function
#        
#          - moves a non-collection resource destPath.
#          - MUST NOT move associated locks.
#            Instead, if the source (or children thereof) have locks, then
#            these locks should be removed.
#          - SHOULD maintain associated live properties, when applicable
#            See http://www.webdav.org/specs/rfc4918.html#dav.properties
#          - MUST maintain associated dead properties
#          - raises HTTP_FORBIDDEN for read-only resources
#          - raises HTTP_INTERNAL_ERROR on error
#        
#        This method MUST be implemented by all providers that support write 
#        access.
#        """
#        self.copyMoveSingle(destPath)
#        self.delete()    


#===============================================================================
# DAVProvider
#===============================================================================

class DAVProvider(object):
    """Abstract base class for DAV resource providers.
    
    There will be only one DAVProvider instance per share (not per request).
    """
    def __init__(self):
        self.mountPath = ""
        self.sharePath = None 
        self.lockManager = None
        self.propManager = None 
        self.verbose = 2

        self._count_getResourceInst = 0
        self._count_getResourceInstInit = 0
#        self.caseSensitiveUrls = True

    
    def __repr__(self):
        return self.__class__.__name__


    def setMountPath(self, mountPath):
        """Set application root for this resource provider.
        
        This is the value of SCRIPT_NAME, when WsgiDAVApp is called.
        """
        assert mountPath in ("", "/") or not mountPath.endswith("/")
        self.mountPath = mountPath

    
    def setSharePath(self, sharePath):
        """Set application location for this resource provider.
        
        @param sharePath: a UTF-8 encoded, unquoted byte string.
        """
        if isinstance(sharePath, unicode):
            sharePath = sharePath.encode("utf8")
        assert sharePath=="" or sharePath.startswith("/")
        if sharePath == "/":
            sharePath = ""  # This allows to code 'absPath = sharePath + path'
        assert sharePath in ("", "/") or not sharePath.endswith("/")
        self.sharePath = sharePath
        

    def setLockManager(self, lockManager):
#        assert isinstance(lockManager, LockManager)
        self.lockManager = lockManager


    def setPropManager(self, propManager):
#        assert isinstance(lockManager, PropManager)
        self.propManager = propManager

        
    def refUrlToPath(self, refUrl):
        """Convert a refUrl to a path, by stripping the share prefix.
        
        Used to calculate the <path> from a storage key by inverting getRefUrl().
        """
        return "/" + urllib.unquote(util.lstripstr(refUrl, self.sharePath)).lstrip("/")


    def getResourceInst(self, path):
        """Return a DAVResource object for path.

        Should be called only once per request and resource::
            
            res = provider.getResourceInst(path)
            if res and not res.isCollection:
                print res.getContentType()
        
        If <path> does not exist, None is returned.
        
        See DAVResource for details.

        This method MUST be implemented.
        """
        raise NotImplementedError()


    def exists(self, path):
        """Return True, if path maps to an existing resource.

        This method should only be used, if no other information is queried
        for <path>. Otherwise a DAVResource should be created first.
        
        This method SHOULD be overridden by a more efficient implementation.
        """
        return self.getResourceInst(path) is not None


    def isCollection(self, path):
        """Return True, if path maps to a collection resource.

        This method should only be used, if no other information is queried
        for <path>. Otherwise a DAVResource should be created first.
        """
        res = self.getResourceInst(path)
        return res and res.isCollection