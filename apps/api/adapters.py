import copy
import json

from django.contrib.messages import ERROR, get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.forms.models import model_to_dict
from django.http import JsonResponse, QueryDict
from django.shortcuts import render as django_render
from django.urls import resolve, Resolver404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response


def render_workflow(request, template_name, context=None, *args, **kwargs):
    if not getattr(request, '_token_workflow', False):
        return django_render(request, template_name, context, *args, **kwargs)
    context = context or {}
    errors = {}
    for name, value in context.items():
        if hasattr(value, 'is_bound') and value.is_bound and value.errors:
            errors[name] = value.errors.get_json_data()
    return JsonResponse({'errors': errors} if errors else {'status': 'ok'}, status=400 if errors else 200)


def invoke_workflow(request, view_class, *, kwargs=None, instance=None, partial=False, method='post'):
    if not isinstance(request.data, dict):
        raise ValidationError({'detail': 'Expected a JSON object.'})
    payload = {}
    if partial and instance is not None:
        payload.update(model_to_dict(instance))
    payload.update(request.data)
    form_data = QueryDict(mutable=True)
    for key, value in payload.items():
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        form_data.setlist(key, [
            str(item.pk) if hasattr(item, 'pk') else
            ('true' if item else 'false') if isinstance(item, bool) else
            json.dumps(item) if isinstance(item, dict) else str(item)
            for item in values
        ])
    web_request = copy.copy(request._request)
    web_request.user = request.user
    web_request.method = method.upper()
    web_request._token_workflow = True
    web_request._body = json.dumps(request.data).encode()
    web_request.POST = form_data
    query = request.query_params.copy()
    if method == 'get':
        query.update(form_data)
    web_request.GET = query
    web_request.session = {}
    web_request._messages = FallbackStorage(web_request)
    handler = view_class()
    handler.setup(web_request, **(kwargs or {}))
    try:
        response = handler.dispatch(web_request, **(kwargs or {}))
    except (ValueError, TypeError):
        raise ValidationError({'detail': 'Invalid workflow input.'})
    context = getattr(response, 'context_data', None)
    if context is not None:
        response = render_workflow(web_request, '', context)
    notices = list(get_messages(web_request))
    errors = [str(message) for message in notices if message.level >= ERROR]
    if errors:
        return Response({'errors': errors}, status=400)
    if isinstance(response, JsonResponse):
        return Response(json.loads(response.content), status=response.status_code)
    if 300 <= response.status_code < 400:
        result = {'status': 'ok', 'messages': [str(message) for message in notices]}
        obj = getattr(handler, 'object', None)
        if obj is not None:
            result['id'] = obj.pk
        elif response.get('Location'):
            try:
                redirected = resolve(response['Location'])
                if 'pk' in redirected.kwargs:
                    result['id'] = redirected.kwargs['pk']
            except Resolver404:
                pass
        return Response(result)
    if response.status_code >= 400:
        return Response({'detail': response.reason_phrase}, status=response.status_code)
    raise ValidationError({'detail': 'Workflow did not return a supported API response.'})