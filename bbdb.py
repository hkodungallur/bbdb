#!/usr/bin/python
import sys
import os
import traceback
import time
import datetime
import re
import json
import requests
import logging
import xml.etree.ElementTree as ET
from git import Repo
from db import DB
from jira import JIRA
from jira.exceptions import JIRAError
from logging.handlers import TimedRotatingFileHandler

#TODO - make build-team-manifest clone location
#       configurable
_GITREPO = Repo('build-team-manifests')
_REMOTES = {
    'blevesearch': 'https://api.github.com/repos/blevesearch/',
    'couchbase': 'https://api.github.com/repos/couchbase/',
    'couchbase-priv': 'https://api.github.com/repos/couchbase/',
    'couchbasedeps': 'https://api.github.com/repos/couchbasedeps/',
    'couchbaselabs': 'https://api.github.com/repos/couchbaselabs/',
    }
_JIRA_PATTERN = r'(\b[A-Z]+-\d+\b)'
prod_branch_map = {
    'watson-4.5.0': 'master',
}
start_build_number = {
    'master': 1,
    'watson-dp1': 1300,
    }
special_previous_builds = {
}
btm_manifest_map = {
    'couchbase-server/watson/4.5.0.xml': ('watson-4.5.0', 'watson.xml'),
    'branch-master.xml': ('master', 'couchbase-server/master.xml'),
    'couchbase-server/sherlock/4.1.1.xml': ('sherlock-4.1.1', 'sherlock.xml'),
    'couchbase-server/watson/4.5.1.xml': ('watson-4.5.1', 'watson.xml'),
}
update_top = []
update_distro = []


_GITHUB_TOKEN = ''
with open(os.path.join(os.path.expanduser('~'), '.ssh/githubtoken')) as F:
    _GITHUB_TOKEN = F.read().strip()

_BLDHISTORY_BUCKET = 'couchbase://localhost/build-history'

class BuildPoller():
    def __init__(self, log_file='build_poller.log', log_level='DEBUG', loop=True, releases=[]):
        self.logger = self._init_logger(log_file, log_level)
        self.bldDB = DB(_BLDHISTORY_BUCKET)
        self.jira = JIRA( { 'server': 'https://issues.couchbase.com/' } )
        self.constants = None
        self.all_releases = None
        self._read_poll_info_from_db()

        self.loop = loop
        self.releases = releases
        if not self.releases:
            self.releases = self.constants['releases']['codes']

    def poll(self):
        while True:
            self.logger.debug('Begin polling at {}'.format(time.ctime()))
            for rel in self.releases:
                self.logger.debug('polling release {}'.format(rel))
                try:
                    #build
                    top_url = self.constants['build_urls'][rel]['top_level']
                    unix_url = self.constants['build_urls'][rel]['unix']
                    win_url = self.constants['build_urls'][rel]['windows']
                    self._poll_top_level(top_url)
                    self._poll_distros(unix_url)
                    self._poll_distros(win_url)

                    #unit-tests
                    unit_urls = self.constants['unit_test_urls']
                    for url in unit_urls:
                        self._poll_unit_results(url)

                    #sanity-test
                    sanity_matrix_urls = self.constants['sanity_test_urls']
                    for url in sanity_matrix_urls:
                        self._poll_build_sanity_results(url)
                except Exception, e:
                    self.logger.error("Exception during polling. But, ignore and repeat:")
                    self.logger.error(e)

            self.logger.debug('End polling at {}'.format(time.ctime()))

            if not self.loop:
                break
            time.sleep(300)
            self._read_poll_info_from_db()

    def query(self):
        pass

    def _poll_top_level(self, url):
        res = self._get_js(url)
        if not res:
            self.logger.warning('Nothing to do since Jenkins returned empty')
            return

        j = res.json()
        poll_from = int(j['lastBuild']['number'])
        poll_till = 0
        if poll_from > 200:
            poll_till = poll_from -  200

        for b in range(poll_from, poll_till, -1):
            ret = self._parse_one_top_build(url, b)
            if not ret:
                self.logger.debug('Reached latest top level build already saved')
                break
            else:
                if ret.split('-')[1] == poll_till:
                    self.logger.debug('poll_top_level: reached end. stopping.')
                    break

    def _parse_one_top_build(self, url, bnum, force=False, version_filter=''):
        self.logger.debug('_parse_one_top_build: url: {}, build_num: {}'.format(url, bnum))
        bldurl_fmt = '{}/{}'
        envurl_fmt = bldurl_fmt + '/injectedEnvVars'

        bldurl = bldurl_fmt.format(url, bnum)
        res = self._get_js(bldurl)
        if not res:
            self.logger.warning('no info from jenkins for {}'.format(bldurl))
            return
        j = res.json()
        build = {}
        build['timestamp'] = j['timestamp']

        envurl = envurl_fmt.format(url, bnum)
        res = self._get_js(envurl)

        if res:
            j = res.json()
            try:
                #sherlock builds have MANIFEST_FILE, watson and spock have MANIFEST
                build['manifest'] = ""
                if j['envMap'].has_key('MANIFEST'):
                    build['manifest'] = j['envMap']['MANIFEST']
                elif j['envMap'].has_key('MANIFEST_FILE'):
                    build['manifest'] = j['envMap']['MANIFEST_FILE']
                build['build_num'] = int(j['envMap']['BLD_NUM'])
                build['version'] = j['envMap']['VERSION']
                if version_filter:
                    if build['version'] != version_filter:
                        self.logger.info('Version filter is set to {}; this build is version {}. Ignoring.'.format(version_filter, build['version']))
                        return '0-0'

                #spock builds are not having manifest_sha env variable
                build['manifest_sha'] = ""
                if j['envMap'].has_key('MANIFEST_SHA'):
                    build['manifest_sha'] = j['envMap']['MANIFEST_SHA']
                else:
                    build['manifest_sha'] = self._get_manifest_sha(build['manifest'],
                                                                   build['timestamp'],
                                                                   build['version']+'-'+str(build['build_num']))

                build['job_build_num'] = j['envMap']['BUILD_NUMBER']
                build['product_branch'] = j['envMap']['PRODUCT_BRANCH']
                if prod_branch_map.has_key(build['product_branch']):
                    map_branch = prod_branch_map[build['product_branch']]
                    build['product_branch'] = map_branch

                #UNIT_TEST was available only for watson/4.5.0; for sherlock we never
                #did unit test, all other >4.5.0 we are moving it out to a separate job
                build['unit'] = "false"
                if j['envMap'].has_key('UNIT_TEST'):
                    build['unit'] = j['envMap']['UNIT_TEST']
            except KeyError, e:
                self.logger.error("_parse_one_top_build: unknown key:" + e.message)
                return '0-0'

        in_build = build['version'] + '-' + str(build['build_num'])
        if not force and self.bldDB.doc_exists(in_build):
            #already there
            return None

        build['commits'] = []
        if (not start_build_number.has_key(build['product_branch'])) or \
           (start_build_number[build['product_branch']] != build['build_num'] and build['manifest_sha']):
            changes, adds, deletes = self._commits(in_build, build['manifest_sha'], build['manifest'])
            build['commits'] = changes + adds
            build['repo_deleted'] = deletes
        build['passed'] = []
        build['failed'] = []
        build['incomplete'] = []
        build['type'] = 'top_level_build'
        self.logger.debug('inserting top level build {} into db'.format(in_build))
        return self.bldDB.insert_build_history(build, force)

    def _poll_distros(self, url):
        incomplete = self.bldDB.get_incomplete_builds()
        for u in incomplete:
            u = u.strip('/')
            baseurl = u[0:u.rfind('/')]
            bnum = u[u.rfind('/')+1:]
            self.logger.debug('{} - polling incomplete build'.format(baseurl))
            self._parse_one_distro(baseurl, bnum, True)

        res = self._get_js(url)
        if not res:
            self.logger.warning('Nothing to do since Jenkins returned empty')
            return
        j = res.json()
        poll_from = int(j['lastBuild']['number'])
        poll_till = 0
        if poll_from > 1500:
            poll_till = poll_from - 1500

        for b in range(poll_from, poll_till, -1):
            ret = self._parse_one_distro(url, b)
            if not ret:
                self.logger.debug('{} - reached latest distro build already saved'.format(url.split('/')[-1]))
                break
            else:
                if ret.split('-')[1] == poll_till:
                    self.logger.debug('{} - reached end'.format(url.split('/')[-1]))
                    break

    def _parse_one_distro(self, url, bnum, update=False):
        self.logger.debug('poll_one_distro: url:{}, bnum: {}'.format(url, bnum))
        bldurl_fmt = '{}/{}'
        envurl_fmt = '{}/{}/injectedEnvVars'
        bldurl = bldurl_fmt.format(url, bnum)
        res = self._get_js(bldurl)
        if not res:
            self.logger.warning('no info from jenkins for {}'.format(bldurl))
            return
        j = res.json()
        dbuild = {}
        dbuild['timestamp'] = j['timestamp']
        dbuild['duration'] = j['duration']
        dbuild['result'] = j['result']
        dbuild['slave'] = j['builtOn']
        dbuild['type'] = 'distro_level_build'
        envurl = envurl_fmt.format(url, bnum)
        res = self._get_js(envurl)
        if not res:
            self.logger.warning('no info from jenkins for {}'.format(envurl))
            return
        if res:
            e = res.json()
            dbuild['build_num'] = int(e['envMap']['BLD_NUM'])
            dbuild['job_build_num'] = e['envMap']['BUILD_NUMBER']
            dbuild['version'] = e['envMap']['VERSION']
            dbuild['unit'] = "false"
            if e['envMap'].has_key('UNIT_TEST'):
                dbuild['unit'] = e['envMap']['UNIT_TEST']

            dbuild['edition'] = e['envMap']['EDITION']
            if url.find('windows') != -1:
                arch = 'amd64'
                if e['envMap'].has_key('ARCHITECTURE'):
                    arch = e['envMap']['ARCHITECTURE']
                dbuild['distro'] = 'win-' + arch
            else:
                if e['envMap'].has_key('DISTRO'):
                    dbuild['distro'] = e['envMap']['DISTRO']
                elif e['envMap'].has_key('PLATFORM'):
                    dbuild['distro'] = e['envMap']['PLATFORM']

            dbuild['url'] = e['envMap']['BUILD_URL']

        for a in j['actions']:
            if a.has_key('totalCount'):
                dbuild['testcount'] = a['totalCount']
                dbuild['failedtests'] = a['failCount']
                dbuild['skiptests'] = a['skipCount']
                uniturl = (dbuild['url'] + '/' + a['urlName']).format(dbuild['build_num'])
                dbuild['test_report_url'] = uniturl
                tests = self._parse_tests(uniturl)
                unit = {}
                unit['build_num'] = dbuild['build_num']
                unit['version'] = dbuild['version']
                unit['edition'] = dbuild['edition']
                unit['distro'] = dbuild['distro']
                unit['tests'] = tests
                unit['type'] = 'test_run'
                self.bldDB.insert_test_history(unit, test_type='unit')

        if dbuild['build_num'] in update_distro:
            docid = self.bldDB.insert_distro_history(dbuild, True)
        else:
            docid = self.bldDB.insert_distro_history(dbuild, update)
        buildid = dbuild['version'] + '-' + str(dbuild['build_num'])
        current_result = 'incomplete'
        if dbuild['result']:
            if dbuild['result'] == "SUCCESS":
                current_result = 'passed'
            else:
                current_result = 'failed'
        if docid:
            self.bldDB.update_distro_result(buildid, docid, current_result)
        return docid

    def _parse_tests(self, url, sanity=False):
        res = self._get_js(url)
        if not res:
            self.logger.warning('no info from jenkins for {}'.format(url))
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
                if sanity:
                    n, p = c['name'].split(',', 1)
                    case['name'] = n
                    case['params'] = p
                else:
                    case['name'] = c['name']
                    case['params'] = ''
                case['duration'] = c['duration']
                case['status'] = c['status']
                case['failed_since'] = c['failedSince']
                cases.append(case)
            suite['cases'] = cases
            units.append(suite)
        return units

    def _comment_on_ticket(self, commit):
      """
      Adds a comment from Build Team onto the Jira ticket regarding a commit.
      """
      # Don't comment about master builds.
      if commit['in_build'][0].startswith("0.0.0"):
        return
      for ticket in commit['fixes']:
        jticket = None
        try:
          jticket = self.jira.issue(ticket)
        except JIRAError as e:
          if e.status_code == 404:
            self.logger.info("commit references non-existent ticket {}".format(ticket))
          else:
            self.logger.warning("error loading JIRA issue {}: {}".format(ticket, e.text))

        if jticket is not None:
          self.jira.add_comment(jticket,
            "Build {} contains {} commit {} with commit message:\n{}\n{}".format(
              commit['in_build'][0],
              commit['repo'],
              commit['sha'],
              commit['message'].split('\n', 1)[0],
              commit['url']))

    def _handle_commit(self, repo, in_build, c):
      """
      Constructs a commit object and inserts it into the database.
      Also updates Jira for any fixed tickets.
      Returns the new database doc ID.
      """
      commit = {}
      commit['in_build'] = [in_build]
      commit['repo'] = repo
      commit['sha'] = c['sha']
      commit['committer'] = c['commit']['committer']
      commit['author'] = c['commit']['author']
      commit['url'] = c['html_url']
      commit['message'] = c['commit']['message']
      commit['type'] = 'commit'
      commit['fixes'] = self.get_fixed_jiras(commit['message'])
      self.logger.debug("insert commit {}-{} into db".format(repo, c['sha']))
      self.logger.debug(json.dumps(commit, indent=2))
      self._comment_on_ticket(commit)
      return self.bldDB.insert_commit(commit)

    def _commits(self, in_build, man_sha, man_file, branch='master'):
        self.logger.debug('_commits: in_build {}, man_sha {}, man_file {}'.format(in_build, man_sha, man_file))
        version, bnum = in_build.split('-')
        prv_bnum = str(int(bnum)-1)
        if special_previous_builds.has_key(bnum):
            prv_bnum = special_previous_builds[bnum]
        doc = self.bldDB.doc_exists(version + '-' + prv_bnum)
        if doc:
            prv_sha = doc.value['manifest_sha']
        else:
            prv_sha = man_sha+'~1'
        if not prv_sha:
            prv_sha = man_sha+'~1'

        manifest_mapped_branch = branch
        manifest_mapped_file = man_file
        if btm_manifest_map.has_key(man_file):
            manifest_mapped_branch = btm_manifest_map[man_file][0]
            manifest_mapped_file = btm_manifest_map[man_file][1]

        _GITREPO.git.checkout(manifest_mapped_branch)

        o = _GITREPO.remotes.origin
        o.pull()
        m1 = _GITREPO.git.show("%s:%s" % (man_sha, manifest_mapped_file))
        m2 = _GITREPO.git.show("%s:%s" % (prv_sha, manifest_mapped_file))
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
            giturl = _REMOTES[p1list[k][1]] + k + '/compare/' + p2list[k][0] + '...' + p1list[k][0]
            res = requests.get(giturl, headers={'Authorization': 'token {}'.format(_GITHUB_TOKEN)})
            j = res.json()
            cmts = j['commits']
            for c in cmts:
              repo_changes.append(self._handle_commit(k, in_build, c))

        for k in added:
            giturl = _REMOTES[p1list[k][1]] + k + '/commits?sha=' + p1list[k][0]
            res = requests.get(giturl, headers={'Authorization': 'token {}'.format(_GITHUB_TOKEN)})
            j = res.json()
            for c in j:
                repo_added.append(self._handle_commit(k, in_build, c))

        for k in deleted:
            self.logger.debug("repo {} was removed in this commit".format(k))
            repo_deleted.append(k)
        return repo_changes, repo_added, repo_deleted


    def get_fixed_jiras(self, msg):
        fixes = []
        title = msg.split('\n', 1)[0]
        matches = re.findall(_JIRA_PATTERN, title)
        if matches:
            for j in matches:
                fixes.append(j)
        return fixes

    def _poll_build_sanity_results(self, url):
        incomplete = self.bldDB.get_incomplete_sanity_runs()
        for u in incomplete:
            u = u.strip('/')
            baseurl = u[0:u.rfind('/')]
            bnum = u[u.rfind('/')+1:]
            self.logger.debug('polling incomplete sanity run {}'.format(baseurl))
            self._parse_one_build_sanity(baseurl, bnum)

        res = self._get_js(url)
        if not res:
            self.logger.warning('_poll_build_sanity: no info from jenkins for {}'.format(url))
            return
        j = res.json()
        poll_from = int(j['lastBuild']['number'])
        poll_till = poll_from - 25

        for i in range(poll_from, poll_till, -1):
            ret = self._parse_one_build_sanity(url, i)
            if ret == "stop":
                break

    def _parse_one_build_sanity(self, url, jenk_bld):
        burl = url + '/' + str(jenk_bld)
        env_url = url + '/' + str(jenk_bld) + '/injectedEnvVars'
        res = self._get_js(burl)
        if not res:
            self.logger.warning('_poll_build_sanity: no info from jenkins for {}'.format(url))
            return "continue"

        j = res.json()

        res = self._get_js(env_url)
        if not res:
            self.logger.warning('_poll_build_sanity: no info from jenkins for {}'.format(env_url))
            return "continue"

        e = res.json()

        ver = e['envMap']['VERSION']
        bld = e['envMap']['CURRENT_BUILD_NUMBER']
        edition = 'enterprise'

        version = ver + '-' + bld
        bld_doc_ret = self.bldDB.doc_exists(version)
        if not bld_doc_ret:
            self.logger.warning('Could not find build doc with id {}'.format(version))
            return 'continue'

        bld_doc = bld_doc_ret.value
        if bld_doc.has_key('sanity_result'):
            sres = bld_doc['sanity_result']
            if sres != 'INCOMPLETE':
                self.logger.debug('_poll_build_sanity: reached the run that has already been saved')
                return 'stop'
            if sres == 'INCOMPLETE' and j['building']:
                self.logger.debug('_poll_build_sanity: this is a run that is still running {}'.format(burl))
                return 'continue'
        elif j['building']:
            bld_doc['sanity_result'] = 'INCOMPLETE'
            bld_doc['sanity_url'] = burl
            self.logger.debug('update db - incomplete build_sanity for {}'.format(version))
            self.logger.debug(bld_doc)
            self.bldDB.insert_build_history(bld_doc, True)
            return "continue"

        overall_result = 'PASSED' # only centos considered
        for r in j['runs']:
            if str(r['number']) != jenk_bld:
                continue

            rurl = r['url']
            res = self._get_js(rurl)
            j = res.json()

            env_url = r['url'] + 'injectedEnvVars'
            res = self._get_js(env_url)
            e = res.json()

            distro = e['envMap']['DISTRO']
            if distro == 'ubuntu14':
                distro = 'ubuntu14.04'
            elif distro == 'win64':
                distro = 'win-amd64'
            clust_type = e['envMap']['TYPE']

            distro_build = version + '-' + distro + '-' + edition
            dbuild_ret  = self.bldDB.doc_exists(distro_build)
            if not dbuild_ret:
                self.logger.warning('_poll_one_build_sanity: Could not find distro build doc with id {}'.format(distro_build))
                return 'continue'


            dbuild = dbuild_ret.value
            tc = 0
            if dbuild.has_key('sanity_testcount'):
                tc = dbuild['sanity_testcount']
            fc = 0
            if dbuild.has_key('sanity_failedtests'):
                fc = dbuild['sanity_failedtests']
            sc = 0
            if dbuild.has_key('sanity_skiptests'):
                fc = dbuild['sanity_skiptests']
            res_key = 'sanity_result_'+ clust_type
            for a in j['actions']:
                if a.has_key('totalCount'):
                    dbuild['sanity_testcount'] = tc + a['totalCount']
                    dbuild['sanity_failedtests'] = fc + a['failCount']
                    dbuild['sanity_skiptests'] = sc + a['skipCount']

                    if j['result'] == 'FAILURE' or j['result'] == 'UNSTABLE':
                        dbuild[res_key] = 'FAILED'
                    elif j['result'] == 'SUCCESS':
                        dbuild[res_key] = 'PASSED'
                    else:
                        dbuild[res_key] = 'FAILED'
                    if distro == 'centos7' and dbuild[res_key] == 'FAILED':
                        overall_result = 'FAILED'
                    break

            sanity_tests = self._parse_tests(r['url'] + 'testReport', sanity=True)
            stests = {}
            docid = '{}-{}-{}-enterprise-sanity-tests'.format(ver, bld, distro)
            sdoc = self.bldDB.doc_exists(docid)
            update = False
            if sdoc:
                update = True
                stests = sdoc.value
                stests[clust_type+'_tests'] = sanity_tests
                if stests['result'] == 'FAILED' or j['result'] == 'FAILED':
                    stests['result'] = 'FAILED'
            else:
                stests['build_num'] = bld
                stests['version'] = ver
                stests['edition'] = edition
                stests['distro'] = distro
                stests['type'] = 'build_sanity_run'
                stests[clust_type+'_tests'] = sanity_tests
                stests['result'] = j['result']

            docId = self.bldDB.insert_test_history(stests, test_type='build_sanity', update=update)
            self.logger.info('_poll_one_sanity: Added sanity test result for: {}'.format(docId))
            #self.logger.info('Added sanity test result for: {}'.format(json.dumps(stests, indent=1)))

            docId = self.bldDB.insert_distro_history(dbuild, True)
            self.logger.info('_poll_one_sanity: Updated distro build result for: {}'.format(docId))
            #self.logger.info('Updated distro build result for: {}'.format(json.dumps(dbuild, indent=1)))

        bld_doc['sanity'] = 'true'
        bld_doc['sanity_url'] = burl
        bld_doc['sanity_result'] = overall_result
        docId = self.bldDB.insert_build_history(bld_doc, True)
        self.logger.info('_poll_one_sanity: Updated build result for {}'.format(docId))
        #self.logger.info('Updated top level build for: {}'.format(json.dumps(bld_doc, indent=1)))

    def _poll_unit_results(self, url):
        incomplete = self.bldDB.get_incomplete_unit_runs()
        for u in incomplete:
            u = u.strip('/')
            baseurl = u[0:u.rfind('/')]
            bnum = u[u.rfind('/')+1:]
            self.logger.debug('polling incomplete unit test run {}'.format(url))
            self._parse_one_unit(baseurl, bnum)

        res = self._get_js(url)
        if not res:
            self.logger.warning('_poll_unit_result: no info from jenkins for {}'.format(url))
            return
        j = res.json()
        poll_from = int(j['lastBuild']['number'])
        poll_till = poll_from - 2

        for i in range(poll_from, poll_till, -1):
            ret = self._parse_one_unit(url, i)
            if ret == "stop":
                break

    def _parse_one_unit(self, url, jenk_bld):
        burl = url + '/' + str(jenk_bld)
        env_url = url + '/' + str(jenk_bld) + '/injectedEnvVars'
        res = self._get_js(burl)
        if not res:
            self.logger.warning('_parse_one_unit: no info from jenkins for {}'.format(url))
            return "continue"

        j = res.json()

        res = self._get_js(env_url)
        if not res:
            self.logger.warning('_parse_one_unit: no info from jenkins for {}'.format(env_url))
            return "continue"

        e = res.json()

        ver = e['envMap']['VERSION']
        bld = e['envMap']['BLD_NUM']
        edition = 'enterprise'
        distro = e['envMap']['DISTRO']
        if distro == 'ubuntu14':
            distro = 'ubuntu14.04'
        elif distro == 'ubuntu12':
            distro = 'ubuntu12.04'
        elif distro == 'win64':
            distro = 'win-amd64'

        version = ver + '-' + bld
        distro_build = version + '-' + distro + '-' + edition

        docid = distro_build + '-tests'
        test_doc = self.bldDB.doc_exists(docid)
        if test_doc:
            self.logger.debug('_parse_one_unit: reached the unit test run that has already been saved')
            return "stop"

        bld_doc_ret = self.bldDB.doc_exists(version)
        if not bld_doc_ret:
            self.logger.warning('Could not find build doc with id {}'.format(version))
            return 'continue'

        bld_doc = bld_doc_ret.value
        if j['building']:
            bld_doc['unit_result'] = 'INCOMPLETE'
            if bld_doc.has_key('unit_urls'):
                uurls = bld_doc['unit_urls']
                found = False
                for uurl in uurls:
                    if uurl['url'] == burl:
                        found = True
                        break
                if not found:
                    bld_doc['unit_urls'].append({"url": burl, "result": "INCOMPLETE"})
            else:
                bld_doc['unit_urls'] = [{"url": burl, "result": "INCOMPLETE"}]
            self.logger.debug('update db - incomplete unit tests for {}'.format(version))
            self.logger.debug(bld_doc)
            self.bldDB.insert_build_history(bld_doc, True)
            return "continue"

        dbuild_ret  = self.bldDB.doc_exists(distro_build)
        if not dbuild_ret:
            self.logger.warning('_parse_one_unit: Could not find distro build doc with id {}'.format(distro_build))
            return 'continue'

        dbuild = dbuild_ret.value
        tc = 0
        if dbuild.has_key('totalcount'):
            tc = dbuild['totalcount']
        fc = 0
        if dbuild.has_key('failedtests'):
            fc = dbuild['failedtests']
        sc = 0
        if dbuild.has_key('skiptests'):
            fc = dbuild['skiptests']

        res_key = 'unit_result'
        unit_found = False
        for a in j['actions']:
            if a.has_key('totalCount'):
                unit_found = True
                dbuild['testcount'] = tc + a['totalCount']
                dbuild['failedtests'] = fc + a['failCount']
                dbuild['skiptests'] = sc + a['skipCount']

                if j['result'] == 'FAILURE' or j['result'] == 'UNSTABLE':
                    dbuild[res_key] = 'FAILED'
                elif j['result'] == 'SUCCESS':
                    dbuild[res_key] = 'PASSED'
                else:
                    dbuild[res_key] = 'FAILED'
                break

        if not unit_found:
            return

        unit_tests = self._parse_tests(burl + '/testReport')
        utests = {}
        utests['build_num'] = bld
        utests['version'] = ver
        utests['edition'] = edition
        utests['distro'] = distro
        utests['type'] = 'test_run'
        utests['tests'] = unit_tests
        utests['result'] = dbuild[res_key]

        docId = self.bldDB.insert_test_history(utests)
        self.logger.info('_parse_one_unit: Added unit test result for: {}'.format(docId))
        #self.logger.info('_parse_one_unit: Added unit test result for: {}'.format(json.dumps(utests, indent=1)))

        docId = self.bldDB.insert_distro_history(dbuild, True)
        self.logger.info('_parse_one_unit: Updated distro build result for: {}'.format(docId))
        #self.logger.info('_parse_one_unit: Updated distro build result for: {}'.format(json.dumps(dbuild, indent=1)))

        bld_doc['unit'] = 'true'
        if bld_doc.has_key('unit_urls'):
            uurls = bld_doc['unit_urls']
            found = False
            for uurl in uurls:
                if uurl['url'] == burl:
                    uurl["result"] = dbuild[res_key]
                    found = True
                    break
            if not found:
                bld_doc['unit_urls'].append({"url": burl, "result": dbuild[res_key]})
        else:
            bld_doc['unit_urls'] = [{"url": burl, "result": dbuild[res_key]}]

        bld_doc['unit_result'] = 'COMPLETE'
        for unit in bld_doc['unit_urls']:
            if unit['result'] == 'INCOMPLETE':
                bld_doc['unit_result'] = 'INCOMPLETE'

        docId = self.bldDB.insert_build_history(bld_doc, True)
        self.logger.info('_parse_one_unit: Updated build result for {}'.format(docId))
        #self.logger.info('_parse_one_unit: Updated top level build for: {}'.format(json.dumps(bld_doc, indent=1)))

    # NOT USED?
    def _value_from_jenkins_params(self, out_json, param):
        ret = ''
        for a in d['actions']:
            if a.has_key('parameters'):
                params = a['parameters']
                for p in params:
                    if p['name'] == param:
                        ver = p['value']
                        break
        return ret

    def _get_manifest_sha(self, man_file, build_time, version):
        self.logger.debug('_get_manifest_sha: polling github for SHA: man_file {} and version {}'.format(man_file, version))

        mf = man_file
        mb = 'master'
        if btm_manifest_map.has_key(man_file):
            mb = btm_manifest_map[man_file][0]
            mf = btm_manifest_map[man_file][1]

        btime = datetime.datetime.fromtimestamp(build_time/1000)
        until = btime.isoformat()

        giturl = 'https://api.github.com/repos/couchbase/build-team-manifests' + '/commits?until={}&&path={}&&sha={}'.format(until, mf, mb)
        self.logger.debug('_get_manifest_sha: polling url: {}'.format(giturl))

        res = requests.get(giturl, headers={'Authorization': 'token {}'.format(_GITHUB_TOKEN)})
        j = res.json()
        for c in j:
            msg = c['commit']['message']
            #print '[{}]'.format(msg)
            if msg.find(version) != -1:
                self.logger.debug('_get_manifest_sha: got SHA from github: {}'.format(c['sha']))
                return c['sha']

        return ""

    def _init_logger(self, log_file, log_level):
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        level = logging.DEBUG
        if log_level.upper() == "DEBUG":
            level = logging.DEBUG
        elif log_level.upper() == "INFO":
            level = logging.INFO
        elif log_level.upper() == "WARNING":
            level = logging.WARNING
        else:
            level = logging.ERROR

        logger = logging.getLogger()
        logger.setLevel(level)

        handler = TimedRotatingFileHandler(log_file,
                                       when="d",
                                       interval=1,
                                       backupCount=7)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def _read_poll_info_from_db(self):
        consts = self.bldDB.doc_exists('constants')
        all_rels = self.bldDB.doc_exists('all-releases')
        self.constants = consts.value
        self.all_releases = all_rels.value
        self.logger.debug('CONSTANTS: %s' %(str(self.constants)))
        self.logger.debug('ALL_RELEASES: %s' %(str(self.all_releases)))

    def _get_js(self, url, params={"depth" : 0}):
        res = None
        for x in range(5):
            try:
                res = requests.get("%s/%s" % (url, "api/json"), params = params, timeout=3)
                if res:
                    break
            except:
                self.logger.error("url unreachable: %s" % url)
                time.sleep(5)

        return res


if __name__ == "__main__":
    bpoller = BuildPoller()
    bpoller.poll()

    #bpoller = BuildPoller(log_file="c.log")
    #for i in range(566, 400, -1):
    #    bpoller._parse_one_top_build('http://server.jenkins.couchbase.com/view/New%20Builds/job/couchbase-server-build', str(i), True, '0.0.0')

    #bpoller = BuildPoller(log_file="s.log")
    #bpoller._parse_one_build_sanity('http://server.jenkins.couchbase.com/view/build-sanity/job/build_sanity_matrix', 1440)

    #bpoller = BuildPoller(log_file="s.log")
    #bpoller._poll_build_sanity_results('http://server.jenkins.couchbase.com/view/build-sanity/job/build_sanity_matrix')

    #bpoller = BuildPoller(log_file="u.log")
    #bpoller._poll_unit_results('http://cv.jenkins.couchbase.com/view/scheduled-unit-tests/job/unit-simple-test')
