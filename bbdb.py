import requests
import xml.etree.ElementTree as ET
from git import Repo

#TODO - make build-team-manifest clone location
#       configurable
gitrepo = Repo('build-team-manifests')
remotes = {
    'couchbase': 'https://api.github.com/repos/couchbase/',
    'couchbase-priv': 'https://api.github.com/repos/couchbase/',
    'couchbase-deps': 'https://api.github.com/repos/couchbasedeps/',
    }

def getJS(url, params = None):
    res = None
    try:
        res = requests.get("%s/%s" % (url, "api/json"), params = params, timeout=3)
    except:
        print "[Error] url unreachable: %s" % url
        pass

    return res

def commits(sha):
    o = gitrepo.remotes.origin
    o.pull()
    m1 = gitrepo.git.show("%s:%s" % (sha, 'watson.xml'))
    m2 = gitrepo.git.show("%s:%s" % (sha+'~1', 'watson.xml'))
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
        res = requests.get(giturl, headers={'Authorization': 'token 55713dcf178286af6352512270fe84f881d64b30'})
        print res.json()


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

    commits(build['manifest_sha'])
    print build

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
        break

def poll(start_at=0):
    pollTop(start_at)

if __name__ == "__main__":
    poll()
