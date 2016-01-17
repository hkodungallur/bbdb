import sys
import os
import requests
import xml.etree.ElementTree as ET
from git import Repo
from db import DB

#TODO - make build-team-manifest clone location
#       configurable
gitrepo = Repo('build-team-manifests')
remotes = {
    'blevesearch': 'https://api.github.com/repos/blevesearch/',
    'couchbase': 'https://api.github.com/repos/couchbase/',
    'couchbase-priv': 'https://api.github.com/repos/couchbase/',
    'couchbasedeps': 'https://api.github.com/repos/couchbasedeps/',
    'couchbaselabs': 'https://api.github.com/repos/couchbaselabs/',
    }

BLDHISTORY_BUCKET = 'couchbase://localhost:8091/build-history'
bldDB = DB(BLDHISTORY_BUCKET)

TOKEN = ''
with open(os.path.join(os.path.expanduser('~'), '.githubtoken')) as F:
    TOKEN = F.read().strip()

def getJS(url, params = None):
    res = None
    try:
        res = requests.get("%s/%s" % (url, "api/json"), params = params, timeout=3)
    except:
        print "[Error] url unreachable: %s" % url
        pass

    return res

def commits(man_sha):
    o = gitrepo.remotes.origin
    o.pull()
    m1 = gitrepo.git.show("%s:%s" % (man_sha, 'watson.xml'))
    m2 = gitrepo.git.show("%s:%s" % (man_sha+'~1', 'watson.xml'))
    mxml1 = ET.fromstring(m1)
    mxml2 = ET.fromstring(m2)
    p1list = []
    p2list = []
    proj1 = mxml1.findall('project')
    proj2 = mxml2.findall('project')
    for p in proj1:
        n = p.get('name')
        v = p.get('revision')
        r = p.get('remote') or 'couchbase'
        p1list.append((n,v,r))
    for p in proj2:
        n = p.get('name')
        v = p.get('revision')
        r = p.get('remote') or 'couchbase'
        p2list.append((n,v,r))

    changes = list(set(p1list) - set(p2list))
    for p in changes:
        giturl = remotes[p[2]] + p[0] + '/git/commits/' + p[1]
        res = requests.get(giturl, headers={'Authorization': 'token {}'.format(TOKEN)})
        j = res.json()
        commit = {}
        commit['repo'] = p[0]
        commit['sha'] = j['sha']
        commit['committer'] = j['committer']
        commit['author'] = j['author']
        commit['url'] = j['html_url']
        commit['message'] = j['message']
        bldDB.insert_commit(commit)


def pollOne(bnum):
    bldurl = 'http://server.jenkins.couchbase.com/job/watson-build/{}'
    envurl = 'http://server.jenkins.couchbase.com/job/watson-build/{}/injectedEnvVars'
    url = bldurl.format(bnum)
    res = getJS(url, {"depth" : 0})
    if not res:
        print 'no info from jenkins for {}'.format(bnum)
        return
    j = res.json()
    build = {}
    build['timestamp'] = j['timestamp']
    url = envurl.format(bnum)
    res = getJS(url, {"depth" : 0})
    if res:
        j = res.json()
        build['manifest_sha'] = j['envMap']['MANIFEST_SHA']
        build['build_num'] = j['envMap']['BLD_NUM']
        build['job_build_num'] = j['envMap']['BUILD_NUMBER']
        build['version'] = j['envMap']['VERSION']
        build['unit'] = j['envMap']['UNIT_TEST']

    bldDB.insert_build_history(build)
    commits(build['manifest_sha'])

def pollTop(start_at):
    baseurl = 'http://server.jenkins.couchbase.com/job/watson-build'
    res = getJS(baseurl, {"depth" : 0})
    if not res:
        print 'Nothing to do'
        return
    j = res.json()
    end_at = int(j['lastBuild']['number'])

    for b in range(end_at, start_at, -1):
        pollOne(b)

def poll(start_at=0):
    pollTop(start_at)

if __name__ == "__main__":
    poll(600)
