#!/usr/bin/python

import json
import re
import urllib2
from flask import Flask, request, render_template, jsonify
from db import buildDB

app = Flask(__name__)

BLDHISTORY_BUCKET = 'couchbase://cb-bbdb:8091/build-history'
bldDB = buildDB(BLDHISTORY_BUCKET)
_JIRA_PATTERN = r'(\b[A-Z]+-\d+\b)'
_REW_PATTERN = r'(http://review.couchbase.org/\d+)'

@app.route('/')
def index():
    projects = []
    rel_lines = bldDB.get_release_lines()
    for rl in ["spock", "watson", "sherlock"]:
        per_release = rel_lines[rl]
        for pr in per_release:
            recent_builds = bldDB.get_recent_builds(pr['version'])
            p = {}
            p['name'] = pr['name']
            p['manifest'] = pr['input_manifest_file']
            p['builds'] = recent_builds
            p['rl'] = rl
            projects.append(p)
    return render_template('index.html', projects=projects)

@app.route('/changelog')
def changelog():
    return render_template('changelog.html')

@app.route('/getchangelog', methods=['GET'])
def getchangelog():
    ver = request.args.get('rel')
    fromb = request.args.get('fromb')
    tob = request.args.get('tob')
    output = get_cl_from_rest(ver, fromb, tob)
    return render_template('showchangelog.html', cl=output)

def get_cl_from_rest(ver, fb, tb):
    sel_from = int(fb)
    sel_to = int(tb)
    f = urllib2.urlopen("http://172.23.123.43:8282/changelog?ver={0}&from={1}&to={2}".format(ver, sel_from, sel_to))
    ret = json.loads(f.read())
    cl = {}
    for commit in ret['log']:
        if not cl.has_key(commit['repo']):
            cl[commit['repo']] = []
        cl[commit['repo']].append(commit)
    return text_output(cl)

def subs_url(output):
    out1 = re.sub(_JIRA_PATTERN, r'<a href="https://issues.couchbase.com/browse/\1">\1</a>', output)
    out2 = re.sub(_REW_PATTERN, r'<a href="\1">\1</a>', out1)
    return out2

def text_output(cl_dict):
    ret = ""
    keys = cl_dict.keys()
    keys.sort()
    for k in keys:
        ret = ret + "CHANGELOG for %s\n\n" %k
        val = cl_dict[k]
        for v in val:
            ret = ret + " * Commit: <a href='%s'>%s</a> " %(v.get('url', ''), v.get('sha', ''))
            ret = ret + "(in build: %s)\n" %v.get('build_num', '')
            ret = ret + "   Author: %s\n" %v.get('author', '')['name']
            message = subs_url(v.get('message', ''))
            ret = ret + "   %s\n\n\n" %message.replace('\n', '\n   ')

    if not ret:
        ret = "There was an error or there are no changes between these builds"
    return ret


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
