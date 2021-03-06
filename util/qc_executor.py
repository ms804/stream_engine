import importlib
import logging
from collections import defaultdict

import numpy as np

from ooi_data.postgres.model import ParameterFunction
from util.common import log_timing

log = logging.getLogger(__name__)


class QcExecutor(object):
    """
    Class to manage the execution of all necessary QC for a request
    """
    def __init__(self, qc_params, stream_request):
        self.stream_request = stream_request
        self.request_id = stream_request.request_id
        self.qc_params = self._prep(qc_params)

    def _prep(self, params):
        qc_dict = {}
        if params is None:
            return qc_dict

        for qcp in params:
            pk = qcp.get('qcParameterPK', {})
            qcid = pk.get('qcId')
            param = pk.get('parameter')
            stream_p = pk.get('streamParameter')
            val = qcp.get('value')
            val_type = qcp.get('valueType')

            try:
                if val_type == 'INT':
                    val = int(val)
                elif val_type == 'FLOAT':
                    val = float(val)
                elif val_type == 'LIST':
                    val = [float(x) for x in val[1:-1].split()]
            except (ValueError, IndexError):
                val = float('nan')
                log.exception('<%s> Unable to encode QC parameter %r as type %r (%r %r %r)',
                              self.request_id, val, val_type, qcid, param, stream_p)

            qc_dict.setdefault(stream_p, {}).setdefault(qcid, {})[param] = val
        return qc_dict

    def qc_check(self, parameter, dataset):
        qcs = self.qc_params.get(parameter.name)
        if qcs is None:
            return

        if parameter.name not in dataset:
            return

        values = dataset[parameter.name].values
        # don't attempt to run QC against 2-D data
        if len(values.shape) > 1:
            log.error('<%s> Attempted to run QC against >1d data %r %r',
                      self.request_id, parameter.name, values.shape)
            return

        local_qc_args = defaultdict(dict)
        for function_name in qcs:
            # vector qc parameters are the only ones with string values and need populating
            for qcp in qcs[function_name]:
                value = qcs[function_name][qcp]
                if value == 'time':  # populate time vectors with particle times
                    local_qc_args[function_name][qcp] = dataset.time.values
                elif value == 'data':  # populate data vectors with particle data
                    local_qc_args[function_name][qcp] = values
                elif isinstance(value, basestring):
                    if value in dataset:
                        if len(dataset[value].values.shape) > 1:
                            log.error('<%s> Attempted to run QC against >1d data %r %r',
                                      self.request_id, value, dataset[value].values.shape)
                            return
                        local_qc_args[function_name][qcp] = dataset[value].values
                else:
                    local_qc_args[function_name][qcp] = qcs[function_name][qcp]

            if 'strict_validation' not in qcs[function_name]:
                local_qc_args[function_name]['strict_validation'] = False

            try:
                module = importlib.import_module(ParameterFunction.query.filter_by(function=function_name).first().owner)
                results = getattr(module, function_name)(**local_qc_args.get(function_name))

                # Force all QC results to be 0/1 - log if non-binary results received, set all out-of-range to fail(0)
                mask = np.logical_not(np.logical_or(results == 1, results == 0))
                if mask.any():
                    log.error('Received QC non binary QC result from %s value_set %r',
                              function_name, np.unique(results[mask]))
                    results[mask] = 0

                qc_count_name = '%s_qc_executed' % parameter.name
                qc_results_name = '%s_qc_results' % parameter.name

                if qc_count_name not in dataset:
                    dataset[qc_count_name] = ('obs', np.zeros_like(dataset.time.values, dtype=np.uint8), {})
                if qc_results_name not in dataset:
                    dataset[qc_results_name] = ('obs', np.zeros_like(dataset.time.values, dtype=np.uint8), {})

                qc_function = ParameterFunction.query.filter_by(function=function_name).first()
                flag = int(qc_function.qc_flag, 2)
                results *= flag

                dataset[qc_count_name].values |= flag
                dataset[qc_results_name].values |= results.astype(np.uint8)

            except (TypeError, ValueError) as e:
                log.exception('<%s> Failed to execute QC %s %r', self.request_id, function_name, e)
