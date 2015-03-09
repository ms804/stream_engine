#!/usr/bin/env python
import json
import os
import gdata.spreadsheet.service as service
from engine import db, app
from model.preload import Stream, ParameterFunction, FunctionType, Parameter, ParameterType, ValueEncoding, CodeSet, \
    Unit, FillValue


key = app.config['SPREADSHEET_KEY']
use_cache = app.config['USE_CACHED_SPREADSHEET']
cachedir = '.cache'


def sheet_generator(name):
    cache_path = os.path.join(cachedir, name)
    rows = []
    if use_cache and os.path.exists(cache_path):
        try:
            rows.extend(json.load(open(cache_path)))
            print 'used cache'
            for row in rows:
                yield row
            return
        except:
            pass

    print 'fetching from google'
    client = service.SpreadsheetsService()
    for sheet in client.GetWorksheetsFeed(key, visibility='public', projection='basic').entry:
        title = sheet.title.text
        rowid = sheet.id.text.split('/')[-1]

        if title == name:
            for x in client.GetListFeed(key, rowid, visibility='public', projection='values').entry:
                d = {}
                for k, v in x.custom.items():
                    if v.text is not None:
                        d[k] = v.text.strip()
                    else:
                        d[k] = None

                rows.append(d)
                yield d

    if use_cache and not os.path.exists(cachedir):
        os.makedirs(cachedir)
    if use_cache:
        print 'Caching data for future runs'
        json.dump(rows, open(cache_path, 'wb'))


def get_simple_field(field_class, value):
    if value is None:
        return value
    item = db.session.query(field_class).filter(field_class.value == value).first()
    if item is None:
        item = field_class(value=value)
        db.session.add(item)
        db.session.commit()

    return item


def validate_parameter_row(row):
    mandatory = ['id', 'name', 'parametertype', 'valueencoding']
    for field in mandatory:
        if row.get(field) is None:
            return False

    if not row.get('id').startswith('PD'):
        return False

    scenario = row.get('scenario')
    if scenario is not None and 'VOID' in scenario:
        return False

    return True


def validate_stream_row(row):
    mandatory = ['id', 'name', 'parameterids']
    for field in mandatory:
        if row.get(field) is None:
            return False

    if not row.get('id').startswith('DICT'):
        return False

    scenario = row.get('scenario')
    if scenario is not None and 'VOID' in scenario:
        return False

    return True


def validate_parameter_func_row(row):
    mandatory = ['id', 'name', 'functiontype', 'function']
    for field in mandatory:
        if row.get(field) is None:
            return False

    if not row.get('id').startswith('PFID'):
        return False

    scenario = row.get('scenario')
    if scenario is not None and 'VOID' in scenario:
        return False

    return True


def get_function(pfid):
    return ParameterFunction.query.get(pfid)


def get_parameter(pdid):
    return db.session.query(Parameter).get(pdid)


def process_parameters(sheet):
    print 'Processing parameters'
    for row in sheet:
        if validate_parameter_row(row):
            parameter = Parameter()
            parameter.id = int(row.get('id')[2:])
            parameter.name = row.get('name')
            parameter.parameter_type = get_simple_field(ParameterType, row.get('parametertype'))
            parameter.value_encoding = get_simple_field(ValueEncoding, row.get('valueencoding'))
            parameter.code_set = get_simple_field(CodeSet, row.get('codeset'))
            parameter.unit = get_simple_field(Unit, row.get('unitofmeasure'))
            parameter.fill_value = get_simple_field(FillValue, row.get('fillvalue'))
            parameter.display_name = row.get('displayname')
            parameter.precision = row.get('precision')
            parameter.data_product_identifier = row.get('dataproductidentifier')
            parameter.description = row.get('description')
            if row.get('parameterfunctionid') is not None:
                id = row.get('parameterfunctionid')
                if id.startswith('PFID'):
                    parameter.parameter_function = get_function(int(id[4:]))

            if row.get('parameterfunctionmap') is not None:
                try:
                    param_map = row.get('parameterfunctionmap')
                    parameter.parameter_function_map = eval(param_map)
                except SyntaxError as e:
                    print row.get('id'), e

            db.session.add(parameter)
    db.session.commit()


def process_parameter_funcs(sheet):
    print 'Processing parameter functions'
    for row in sheet:
        if validate_parameter_func_row(row):
            func = ParameterFunction()
            func.id = int(row.get('id')[4:])
            func.name = row.get('name')
            func.function_type = get_simple_field(FunctionType, row.get('functiontype'))
            func.function = row.get('function')
            func.owner = row.get('owner')
            func.description = row.get('description')
            db.session.add(func)
    db.session.commit()


def process_streams(sheet):
    print 'Processing streams'
    for row in sheet:
        if validate_stream_row(row):
            stream = Stream()
            stream.id = int(row.get('id')[4:])
            stream.name = row.get('name')
            params = row.get('parameterids').split(',')
            params = [int(p.strip()[2:]) for p in params if p.startswith('PD')]
            params = sorted(list(set(params)))
            for each in params:
                parameter = get_parameter(each)
                if parameter is not None:
                    stream.parameters.append(parameter)
            if len(stream.parameters) > 0:
                db.session.add(stream)

    db.session.commit()


def create_db():
    db.drop_all()
    db.create_all()
    process_parameter_funcs(sheet_generator('ParameterFunctions'))
    process_parameters(sheet_generator('ParameterDefs'))
    process_streams(sheet_generator('ParameterDictionary'))

if __name__ == '__main__':
    create_db()