"""
API for the image classification package

Date: September 2018
Author: Ignacio Heredia
Email: iheredia@ifca.unican.es
Github: ignacioheredia

Notes: Based on https://github.com/indigo-dc/plant-classification-theano/blob/package/plant_classification/api.py

Descriptions:
The API will use the model files inside ../models/api. If not found it will use the model files of the last trained model.
If several checkpoints are found inside ../models/api/ckpts we will use the last checkpoint.

Warnings:
There is an issue of using Flask with Keras: https://github.com/jrosebr1/simple-keras-rest-api/issues/1
The fix done (using tf.get_default_graph()) will probably not be valid for standalone wsgi container e.g. gunicorn,
gevent, uwsgi.
"""

import json
import os
import tempfile
import warnings
from datetime import datetime

import numpy as np
import requests
from werkzeug.exceptions import BadRequest
import tensorflow as tf
from tensorflow.keras.models import load_model
from tensorflow.keras import backend as K

from imgclas import paths, utils, config
from imgclas.data_utils import load_class_names, load_class_info
from imgclas.test_utils import predict
from imgclas.train_runfile import train_fn


########################################################################################################################
# Everything in this block of code is only used for prediction!!

# Set the timestamp
timestamps = os.listdir(paths.get_models_dir())
if 'api' in timestamps:
    TIMESTAMP = 'api'
else:
    TIMESTAMP = sorted(timestamps)[-1]
# TIMESTAMP = 'theano_reproduced_albu'
paths.timestamp = TIMESTAMP

# Set the checkpoint model to use to make the prediction
ckpts =  os.listdir(paths.get_checkpoints_dir())
if 'final_model.h5' in ckpts:
    MODEL_NAME = 'final_model.h5'
else:
    MODEL_NAME = sorted([name for name in ckpts if name.endswith('*.h5')])[-1]
#MODEL_NAME = 'final_model.h5'

TOP_K = 5  # number of top classes predictions to save

# Load the class names and info
class_names = load_class_names()
class_info = None
if 'info.txt' in os.listdir(paths.get_ts_splits_dir()):
    class_info = load_class_info()
    if len(class_info) != len(class_names):
        warnings.warn("""The 'classes.txt' file has a different length than the 'info.txt' file.
        If a class has no information whatsoever you should leave that classes row empty or put a '-' symbol.
        The API will run with no info until this is solved.""")
        class_info = None
if class_info is None:
    class_info = ['' for _ in range(len(class_names))]

# Load training configuration
conf_path = os.path.join(paths.get_conf_dir(), 'conf.json')
with open(conf_path) as f:
    conf = json.load(f)

# Load the model
model = load_model(os.path.join(paths.get_checkpoints_dir(), MODEL_NAME), custom_objects=utils.get_custom_objects())
graph = tf.get_default_graph()

#Allow only certain file extensions
allowed_extensions = set(['png', 'jpg', 'jpeg', 'PNG', 'JPG', 'JPEG'])
########################################################################################################################


def catch_error(f):
    def wrap(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            raise e
    return wrap


def catch_url_error(url_list):

    # Error catch: Empty query
    if not url_list:
        raise BadRequest('Empty query')

    for i in url_list:

        # Error catch: Inexistent url
        try:
            url_type = requests.head(i).headers.get('content-type')
        except:
            raise BadRequest("""Failed url connection:
            Check you wrote the url address correctly.""")

        # Error catch: Wrong formatted urls
        if url_type.split('/')[0] != 'image':
            raise BadRequest("""Url image format error:
            Some urls were not in image format.
            Check you didn't uploaded a preview of the image rather than the image itself.""")


def catch_localfile_error(file_list):

    # Error catch: Empty query
    if not file_list[0].filename:
        raise BadRequest('Empty query')

    # Error catch: Image format error
    for f in file_list:
        extension = f.split('.')[-1]
        if extension not in allowed_extensions:
            raise BadRequest("""Local image format error:
            At least one file is not in a standard image format (jpg|jpeg|png).""")


@catch_error
def predict_url(urls, merge=True):
    """
    Function to predict an url
    """
    catch_url_error(urls)
    with graph.as_default():
        pred_lab, pred_prob = predict(model=model,
                                      X=urls,
                                      CONF=conf,
                                      top_K=TOP_K,
                                      filemode='url',
                                      merge=merge)

    if merge:
        pred_lab, pred_prob = np.squeeze(pred_lab), np.squeeze(pred_prob)

    return format_prediction(pred_lab, pred_prob)


@catch_error
def predict_file(filenames, merge=True):
    """
    Function to predict a local image
    """
    catch_localfile_error(filenames)
    with graph.as_default():
        pred_lab, pred_prob = predict(model=model,
                                      X=filenames,
                                      CONF=conf,
                                      top_K=TOP_K,
                                      filemode='local',
                                      merge=merge)

    if merge:
        pred_lab, pred_prob = np.squeeze(pred_lab), np.squeeze(pred_prob)

    return format_prediction(pred_lab, pred_prob)


@catch_error
def predict_data(images, merge=True):
    """
    Function to predict an image in binary format
    """
    if not isinstance(images, list):
        images = [images]

    filenames = []
    for image in images:
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(image)
        f.close()
        filenames.append(f.name)

    try:
        with graph.as_default():
            pred_lab, pred_prob = predict(model=model,
                                          X=filenames,
                                          CONF=conf,
                                          top_K=TOP_K,
                                          filemode='local',
                                          merge=merge)
    except Exception as e:
        raise e
    finally:
        for f in filenames:
            os.remove(f)

    if merge:
        pred_lab, pred_prob = np.squeeze(pred_lab), np.squeeze(pred_prob)

    return format_prediction(pred_lab, pred_prob)


def format_prediction(labels, probabilities):
    d = {
        "status": "ok",
         "predictions": [],
    }

    for label_id, prob in zip(labels, probabilities):
        name = class_names[label_id]

        pred = {
            "label_id": int(label_id),
            "label": name,
            "probability": float(prob),
            "info": {
                "links": [{"link": 'Google images', "url": image_link(name)},
                          {"link": 'Wikipedia', "url": wikipedia_link(name)}],
                'metadata': class_info[label_id],
            },
        }
        d["predictions"].append(pred)
    return d


def image_link(pred_lab):
    """
    Return link to Google images
    """
    base_url = 'https://www.google.es/search?'
    params = {'tbm':'isch','q':pred_lab}
    link = base_url + requests.compat.urlencode(params)
    return link


def wikipedia_link(pred_lab):
    """
    Return link to wikipedia webpage
    """
    base_url = 'https://en.wikipedia.org/wiki/'
    link = base_url + pred_lab.replace(' ', '_')
    return link


def metadata():
    d = {
        "author": None,
        "description": None,
        "url": None,
        "license": None,
        "version": None,
    }
    return d


@catch_error
def train(user_conf={}):
    """
    Parameters
    ----------
    user_conf : dict
        Json dict (created with json.dumps) with the user's configuration parameters that will replace the defaults.
        Must be loaded with json.loads()
        For example:
            user_conf={'num_classes': 'null', 'lr_step_decay': '0.1', 'lr_step_schedule': '[0.7, 0.9]', 'use_early_stopping': 'false'}
    """
    CONF = config.CONF

    # Update the conf with the user input
    for group, val in sorted(CONF.items()):
        for g_key, g_val in sorted(val.items()):
            g_val['value'] = json.loads(user_conf[g_key])

    # Check the configuration
    try:
        config.check_conf(conf=CONF)
    except Exception as e:
        raise BadRequest(e)

    CONF = config.conf_dict(conf=CONF)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S').replace(' ', '_')

    config.print_conf_table(CONF)
    K.clear_session() # remove the model loaded for prediction
    train_fn(TIMESTAMP=timestamp, CONF=CONF)
