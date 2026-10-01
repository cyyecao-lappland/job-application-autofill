"""Sequential CLI requests with imports reused; each request retains CLI locks.

No state, profile, graph, or browser result is cached between requests. A caller
must reconcile a lost response through the original journals, never replay it.
"""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import sys

from .application_cli import main


def serve(input_stream, output_stream):
    for line in input_stream:
        request = json.loads(line)
        captured, errors = io.StringIO(), io.StringIO()
        try:
            arguments = request['args']
            if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
                raise ValueError('worker_arguments_invalid')
            with redirect_stdout(captured), redirect_stderr(errors):
                main(arguments)
            response = {'id': request['id'], 'result': json.loads(captured.getvalue())}
        except (Exception, SystemExit) as error:
            response = {'id': request.get('id'), 'error': str(error),
                        'diagnostic': errors.getvalue() or captured.getvalue()}
        output_stream.write(json.dumps(response, ensure_ascii=False) + '\n')
        output_stream.flush()


if __name__ == '__main__':
    serve(sys.stdin, sys.stdout)
