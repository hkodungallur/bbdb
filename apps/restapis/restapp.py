#!/usr/bin/python
import json
from flask import Flask, request
from flask.json import jsonify
from db import DB

app = Flask(__name__)

db = DB()

@app.route('/vms/get', methods=['GET'])
def provision_vms():
    plat = request.args.get('os', '')
    num = request.args.get('total', 1)
    why = request.args.get('why', '')
    who = request.args.get('who', 'unknown')
    hrs_str = request.args.get('hours', '3')
    hrs_int = int(hrs_str)
    vms = db.provision(plat, num, why, who, hrs_int)
    return jsonify({'vms': vms})

@app.route('/vms/release', methods=['GET'])
def release_vms():
    vms = request.args.getlist('vm')
    vms = db.release(vms)
    return jsonify({'vms': vms})

@app.route('/builds/lastunit', methods=['GET'])
def last_build_unit_tested():
    v = request.args.get('ver', '4.7.0')
    return jsonify({'build_num': db.last_unit(v)})

@app.route('/builds/lastsanity', methods=['GET'])
def last_build_sanity():
    v = request.args.get('ver', '4.7.0')
    r = request.args.get('passed', '0')
    return jsonify({'build_num': db.last_sanity(v, r)})

@app.route('/builds/lastunitsanity', methods=['GET'])
def last_build_with_unit_and_sanity():
    v = request.args.get('ver', '4.7.0')
    return jsonify({'build_num': db.last_unit_plus_sanity(v)})

@app.route('/builds/lastqe', methods=['GET'])
def last_qe():
    v = request.args.get('ver', '4.7.0')
    return jsonify({'build_num': db.last_qe(v)})

@app.route('/builds/totest', methods=['GET'])
def builds_to_test():
    t = request.args.get('type', 'unit')
    v = request.args.get('ver', '4.7.0')
    l  = request.args.get('limit', 3)
    if t == 'unit':
        return jsonify({'build_nums': db.not_yet_unit_tested(v, l)})
    elif t == 'sanity':
        return jsonify({'build_nums': db.not_yet_sanity_tested(v, l)})
    else:
        return jsonify({'build_nums': []})

@app.route('/builds/info', methods=['GET'])
def get_build_info():
    v = request.args.get('ver')
    b  = request.args.get('bnum')
    return jsonify({'build_info': db.get_bld_doc(v, b)})

@app.route('/changelog', methods=['GET'])
def get_log():
    ver = request.args.get('ver')
    frm = request.args.get('from')
    to = request.args.get('to')
    clog = db.get_log(ver, frm, to)
    return jsonify({'log': clog})

@app.route('/builds/qekickoff', methods=['GET'])
def qe_kicked_off():
    ver = request.args.get('ver')
    bnum = request.args.get('bnum')
    ret = db.qe_kicked_off(ver+'-'+bnum)
    return jsonify({'ver': ret})

@app.route('/builds/hasticket', methods=['GET'])
def has_ticket():
    t = request.args.get('id')
    ret = db.ticket_in_build(t)
    return jsonify({'builds': ret})

@app.route('/builds/tickets', methods=['GET'])
def included_tickets():
    ver = request.args.get('ver')
    bnum = request.args.get('bnum')
    ret = db.fixes_in_build(ver, bnum)
    return jsonify({'tickets': ret})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)
