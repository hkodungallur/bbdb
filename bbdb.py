#!python
import sys
import os
import time
import json
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

def commits(in_build, man_sha):
    o = gitrepo.remotes.origin
    o.pull()
    m1 = gitrepo.git.show("%s:%s" % (man_sha, 'watson.xml'))
    m2 = gitrepo.git.show("%s:%s" % (man_sha+'~1', 'watson.xml'))
    mxml1 = ET.fromstring(m1)
    mxml2 = ET.fromstring(m2)
    p1list = {}
    p2list = {}
    proj1 = mxml1.findall('project')
    proj2 = mxml2.findall('project')
    for p in proj1:
        n = p.get('name')
        v = p.get('revision')
        r = p.get('remote') or 'couchbase'
        p1list[n] = (v,r)
    for p in proj2:
        n = p.get('name')
        v = p.get('revision')
        r = p.get('remote') or 'couchbase'
        p2list[n] = (v,r)

    #changes = list(set(p1list) - set(p2list))
    p1projs = p1list.keys() 
    p2projs = p2list.keys() 
    added = [x for x in p1projs if x not in p2projs]
    deleted = [x for x in p2projs if x not in p1projs]
    common = [x for x in p1projs if x not in added]

    repo_changes = []
    repo_added = []
    repo_deleted = []
    for k in common:
        if p1list[k][0] == p2list[k][0]:
            continue
        giturl = remotes[p1list[k][1]] + k + '/compare/' + p2list[k][0] + '...' + p1list[k][0]
        res = requests.get(giturl, headers={'Authorization': 'token {}'.format(TOKEN)})
        j = res.json()
        cmts = j['commits']
        for c in cmts:
            commit = {}
            commit['in_build'] = [in_build]
            commit['repo'] = k
            commit['sha'] = c['sha']
            commit['committer'] = c['commit']['committer']
            commit['author'] = c['commit']['author']
            commit['url'] = c['html_url']
            commit['message'] = c['commit']['message']
            ret = bldDB.insert_commit(commit)
            repo_changes.append(ret)
    for k in added:
        giturl = remotes[p1list[k][1]] + k + '/commits?sha=' + p1list[k][0]
        res = requests.get(giturl, headers={'Authorization': 'token {}'.format(TOKEN)})
        j = res.json()
        for c in j:
            commit = {}
            commit['in_build'] = [in_build]
            commit['repo'] = k
            commit['sha'] = c['sha']
            commit['committer'] = c['commit']['committer']
            commit['author'] = c['commit']['author']
            commit['url'] = c['html_url']
            commit['message'] = c['commit']['message']
            ret = bldDB.insert_commit(commit)
            repo_added.append(ret)
    for k in deleted:
        giturl = remotes[p2list[k][1]] + k + '/commits/' + p2list[k][0]
        res = requests.get(giturl, headers={'Authorization': 'token {}'.format(TOKEN)})
        j = res.json()
        commit = {}
        commit['in_build'] = in_build
        commit['repo'] = k
        commit['sha'] = c['sha']
        commit['committer'] = c['committer']
        commit['author'] = c['author']
        commit['url'] = c['html_url']
        commit['message'] = c['message']
        ret = bldDB.insert_commit(commit)
        repo_deleted.append(ret)
    return repo_changes, repo_added, repo_deleted


def pollABuild(bnum):
    print 'pollABuild: ',
    print bnum
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
        try:
            build['manifest_sha'] = j['envMap']['MANIFEST_SHA']
            build['build_num'] = j['envMap']['BLD_NUM']
            build['job_build_num'] = j['envMap']['BUILD_NUMBER']
            build['version'] = j['envMap']['VERSION']
            build['unit'] = j['envMap']['UNIT_TEST']
        except KeyError:
            return "0-0"

    in_build = build['version'] + '-' + build['build_num']
    changes, adds, deletes = commits(in_build, build['manifest_sha'])
    build['commits'] = changes + adds + deletes
    return bldDB.insert_build_history(build)

def pollADistro(baseurl, bnum):
    print 'pollADistro : ',
    print bnum
    bldurl = '{}/{}'
    envurl = '{}/{}/injectedEnvVars'
    url = bldurl.format(baseurl, bnum)
    res = getJS(url, {"depth" : 0})
    if not res:
        print 'no info from jenkins for {}'.format(bnum)
        return
    j = res.json()
    dbuild = {}
    dbuild['timestamp'] = j['timestamp']
    dbuild['duration'] = j['duration']
    dbuild['result'] = j['result']
    dbuild['slave'] = j['builtOn']
    url = envurl.format(baseurl, bnum)
    res = getJS(url, {"depth" : 0})
    if res:
        e = res.json()
        dbuild['build_num'] = e['envMap']['BLD_NUM']
        dbuild['job_build_num'] = e['envMap']['BUILD_NUMBER']
        dbuild['version'] = e['envMap']['VERSION']
        dbuild['unit'] = e['envMap']['UNIT_TEST']
        dbuild['edition'] = e['envMap']['EDITION']
        if url.find('windows') != -1:
            dbuild['distro'] = 'win-' + e['envMap']['ARCHITECTURE']
        else:
            dbuild['distro'] = e['envMap']['DISTRO']
        dbuild['url'] = e['envMap']['BUILD_URL']

    for a in j['actions']:
        if a.has_key('totalCount'):
             dbuild['testcount'] = a['totalCount']
             dbuild['failedtests'] = a['failCount']
             dbuild['skiptests'] = a['skipCount']
             uniturl = (dbuild['url'] + '/' + a['urlName']).format(dbuild['build_num'])
             dbuild['test_report_url'] = uniturl
             tests = pollUnit(uniturl)
             unit = {}
             unit['build_num'] = dbuild['build_num']
             unit['version'] = dbuild['version']
             unit['edition'] = dbuild['edition']
             unit['distro'] = dbuild['distro']
             unit['tests'] = tests
             bldDB.insert_unit_history(unit)

    doc_id = dbuild['version'] + '-' + dbuild['build_num']
    return bldDB.insert_distro_history(dbuild)

def pollUnit(url):
    res = getJS(url, {"depth" : 0})
    if not res:
        print 'no info from jenkins for {}'.format(url)
        return
    j = res.json()
    units = []
    for s in j['suites']:
        suite = {}
        suite['suite'] = s['name']
        suite['duration'] = s['duration']
        cases = []
        for c in s['cases']:
            case = {}
            case['name'] = c['name']
            case['duration'] = c['duration']
            case['status'] = c['status']
            case['failed_since'] = c['failedSince']
            cases.append(case)
        suite['cases'] = cases
        units.append(suite)
    return units

def pollTopBuild(start_at):
    baseurl = 'http://server.jenkins.couchbase.com/job/watson-build'
    res = getJS(baseurl, {"depth" : 0})
    if not res:
        print 'Nothing to do'
        return
    j = res.json()
    end_at = int(j['lastBuild']['number'])

    for b in range(end_at, start_at, -1):
        ret = pollABuild(b)
        if not ret:
            print 'pollTopBuild - reached latest build already saved'
            break
        else:
            if ret.split('-')[1] == start_at:
                print 'pollTopBuild - reached minm build number'
                break

def pollDistros(start_at):
    baseurls = ['http://server.jenkins.couchbase.com/job/watson-unix', 
                'http://server.jenkins.couchbase.com/job/watson-windows',
               ] 
    for u in baseurls:
        res = getJS(u, {"depth" : 0})
        if not res:
            print 'Nothing to do'
            return
        j = res.json()
        end_at = int(j['lastBuild']['number'])

        for b in range(end_at, start_at, -1):
            ret = pollADistro(u, b) 
            if not ret:
                print 'pollDistros/{} - reached latest build already saved'.format(u.split('/')[-1])
                break
                pass
            else:
                if ret.split('-')[1] == start_at:
                    print 'pollDistros/{} - reached minm build number'.format(u.split('/')[-1])
                    break

def poll(start_at=0):
    while True:
        pollTopBuild(start_at)
        pollDistros(start_at)
        time.sleep(300)

if __name__ == "__main__":
    #doc_id = '4.5.0-199'
    #if bldDB.doc_exists(doc_id):
    #    print 'heya'
    #else:
    #    print 'ugh'
    poll(499)
