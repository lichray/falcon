"""Includes private helpers for the API class.

Copyright 2013 by Rackspace Hosting, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

"""

import re
from functools import wraps

from falcon import responders, HTTP_METHODS

import falcon.status_codes as status


IGNORE_BODY_STATUS_CODES = set([
    status.HTTP_100,
    status.HTTP_101,
    status.HTTP_204,
    status.HTTP_416,
    status.HTTP_304
])


def prepare_global_hooks(hooks):
    if hooks is not None:
        if not isinstance(hooks, list):
            hooks = [hooks]

        for action in hooks:
            if not hasattr(action, '__call__'):
                raise TypeError('One or more hooks are not callable')

    return hooks


def should_ignore_body(status, method):
    """Return True if the status or method indicates no body, per RFC 2616

    Args:
        status: An HTTP status line, e.g., "204 No Content"

    Returns:
        True if method is HEAD, or the status is 1xx, 204, or 304; returns
        False otherwise.

    """

    return (method == 'HEAD' or status in IGNORE_BODY_STATUS_CODES)


def set_content_length(resp):
    """Set Content-Length when given a fully-buffered body or stream length

    Pre:
        Either resp.body or resp.stream is set
    Post:
        resp contains a "Content-Length" header unless a stream is given, but
        resp.stream_len is not set (in which case, the length cannot be
            derived reliably).
    Args:
        resp: The response object on which to set the content length.

    """

    content_length = 0

    if resp.body is not None:
        # Since body is assumed to be a byte string (str in Python 2, bytes in
        # Python 3), figure out the length using standard functions.
        content_length = len(resp.body)
    elif resp.stream is not None:
        if resp.stream_len is not None:
            # Total stream length is known in advance (e.g., streaming a file)
            content_length = resp.stream_len
        else:
            # Stream given, but length is unknown (dynamically-generated body)
            # ...do not set the header.
            return -1

    resp.set_header('Content-Length', str(content_length))
    return content_length


def get_body(resp):
    """Converts resp content into an iterable as required by PEP 333

    Args:
        resp: Instance of falcon.Response

    Returns:
        * If resp.body is not None, returns [resp.body], encoded as UTF-8.
        * If resp.data is not None, returns [resp.data]
        * If resp.stream is not None, returns resp.stream
        * Otherwise, returns []

    """

    body = resp.body

    if body is not None:
        try:
            return [body.encode('utf-8')]
        except UnicodeDecodeError:
            return [body]

    elif resp.data is not None:
        return [resp.data]

    elif resp.stream is not None:
        return resp.stream

    return []


def compile_uri_template(template=None):
    """Compile the given URI template string into a pattern matcher.

    Currently only recognizes Level 1 URI templates, and only for the path
    portion of the URI.

    See also: http://tools.ietf.org/html/rfc6570

    Args:
        template: A Level 1 URI template. Method responders must accept, as
        arguments, all fields specified in the template (default '/').

    """

    if not isinstance(template, str):
        raise TypeError('uri_template is not a string')

    if not template:
        template = '/'
    elif template != '/' and template.endswith('/'):
        template = template[:-1]

    # Convert Level 1 var patterns to equivalent named regex groups
    escaped = re.sub(r'([\.\(\)\[\]\?\*\+\^\|])', r'\.', template)
    pattern = re.sub(r'{([a-zA-Z][a-zA-Z_]*)}', r'(?P<\1>[^/]+)', escaped)
    pattern = r'\A' + pattern + r'/?\Z'

    return re.compile(pattern, re.IGNORECASE)


def create_http_method_map(resource, before, after):
    """Maps HTTP methods (such as GET and POST) to methods of resource object

    Args:
        resource: An object with "responder" methods, starting with on_*, that
           correspond to each method the resource supports. For example, if a
           resource supports GET and POST, it should define
           on_get(self, req, resp) and on_post(self,req,resp).
        before: An action hooks or list of hooks to be called before each
           on_* responder defined by the resource.

    """

    method_map = {}

    for method in HTTP_METHODS:
        try:
            responder = getattr(resource, 'on_' + method.lower())
        except AttributeError:
            # resource does not implement this method
            pass
        else:
            # Usually expect a method, but any callable will do
            if hasattr(responder, '__call__'):
                responder = _wrap_with_hooks(before, after, responder)
                method_map[method] = responder

    # Attach a resource for unsupported HTTP methods
    allowed_methods = list(method_map.keys())
    responder = responders.create_method_not_allowed(allowed_methods)

    for method in HTTP_METHODS:
        if method not in allowed_methods:
            method_map[method] = responder

    return method_map


#-----------------------------------------------------------------------------
# Helpers
#-----------------------------------------------------------------------------


def _wrap_with_hooks(before, after, responder):
    if after is not None:
        for action in after:
            responder = _wrap_with_after(action, responder)

    if before is not None:
        # Wrap in reversed order to achieve natural (first...last)
        # execution order.
        for action in reversed(before):
            responder = _wrap_with_before(action, responder)

    return responder


def _wrap_with_before(action, responder):
    """Execute the given action function before a bound responder.

    Args:
        action: A function with a similar signature to a resource responder
            method, taking (req, resp, params).
        responder: The bound responder to wrap.

    """

    @wraps(responder)
    def do_before(req, resp, **kwargs):
        action(req, resp, kwargs)
        responder(req, resp, **kwargs)

    return do_before


def _wrap_with_after(action, responder):
    """Execute the given action function after a bound responder.

    Args:
        action: A function with a signature similar to a resource responder
            method, taking (req, resp).
        responder: The bound responder to wrap.

    """

    @wraps(responder)
    def do_after(req, resp, **kwargs):
        responder(req, resp, **kwargs)
        action(req, resp)

    return do_after
