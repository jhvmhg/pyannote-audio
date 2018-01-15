#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2016-2017 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr


from __future__ import division

import yaml
import io
import os.path
import warnings
from glob import glob
import numpy as np
from struct import unpack
import audioread
import librosa
from pyannote.core import SlidingWindow, SlidingWindowFeature
from pyannote.database.util import get_unique_identifier
from pyannote.audio.util import mkdir_p



class PyannoteFeatureExtractionError(Exception):
    pass


def get_audio_duration(current_file):
    """Return audio file duration

    Parameters
    ----------
    current_file : dict
        Dictionary given by pyannote.database.

    Returns
    -------
    duration : float
        Audio file duration.
    """
    path = current_file['audio']

    with audioread.audio_open(path) as f:
        duration = f.duration

    return duration


def get_audio_sample_rate(current_file):
    """Return audio file sampling rate

    Parameters
    ----------
    current_file : dict
        Dictionary given by pyannote.database.

    Returns
    -------
    sample_rate : int
        Sampling rate
    """
    path = current_file['audio']

    with audioread.audio_open(path) as f:
        sample_rate = f.samplerate

    return sample_rate


def read_audio(current_file, sample_rate=None, mono=True):
    """Read audio file

    Parameters
    ----------
    current_file : dict
        Dictionary given by pyannote.database.
    sample_rate: int, optional
        Target sampling rate. Defaults to using native sampling rate.
    mono : int, optional
        Convert multi-channel to mono. Defaults to True.

    Returns
    -------
    y : (n_samples, n_channels) np.array
        Audio samples.
    sample_rate : int
        Sampling rate.

    Notes
    -----
    In case `current_file` contains a `channel` key, data of this (1-indexed)
    channel will be returned.

    """

    if current_file['audio'][-4:] == '.sph':
        from sphfile import SPHFile
        sph = SPHFile(current_file['audio'])
        y = sph.content
        sample_rate_ = sph.format['sample_rate']
        if sample_rate is not None and sample_rate_ != sample_rate:
            y = librosa.resample(y, sample_rate_, sample_rate)
        else:
            sample_rate = sample_rate_

    else:
        y, sample_rate = librosa.load(current_file['audio'],
                                      sr=sample_rate,
                                      mono=False)

    # reshape mono files to (1, n) [was (n, )]
    if y.ndim == 1:
        y = y.reshape(1, -1)

    # extract specific channel if requested
    if 'channel' in current_file:
        channel = current_file['channel']
        y = y[channel - 1, :]

    # convert to mono
    if mono:
        y = librosa.to_mono(y)

    return y.T, sample_rate


class RawAudio(object):
    """

    Parameters
    ----------
    sample_rate: int, optional
        Target sampling rate. Defaults to using native sampling rate.
    mono : int, optional
        Convert multi-channel to mono. Defaults to True.

    """

    def __init__(self, sample_rate=None, mono=True, mu=0., sigma=1.):
        super(RawAudio, self).__init__()
        self.sample_rate = sample_rate
        self.mono = mono
        self.mu = mu
        self.sigma = sigma

    def __call__(self, current_file):

        y, sample_rate = read_audio(current_file,
                                    sample_rate=self.sample_rate,
                                    mono=self.mono)

        sliding_window = SlidingWindow(start=0.,
                                       duration=1./sample_rate,
                                       step=1./sample_rate)

        return SlidingWindowFeature((y - self.mu) / self.sigma,
                                    sliding_window)


class Precomputed(object):
    """Precomputed features

    Parameters
    ----------
    root_dir :
    use_memmap : bool, optional
    mu, sigma : np.array, optional
        Apply mu/sigma normalization.
    """

    def get_path(self, item):
        uri = get_unique_identifier(item)
        path = '{root_dir}/{uri}.npy'.format(root_dir=self.root_dir, uri=uri)
        return path

    def __init__(self, root_dir=None, use_memmap=True,
                 sliding_window=None, dimension=None,
                 mu=None, sigma=None):

        super(Precomputed, self).__init__()
        self.root_dir = root_dir
        self.use_memmap = use_memmap
        self.mu = mu
        self.sigma = sigma

        path = '{root_dir}/metadata.yml'.format(root_dir=self.root_dir)

        if os.path.exists(path):

            with io.open(path, 'r') as f:
                params = yaml.load(f)

            self.dimension_ = params.pop('dimension')
            self.sliding_window_ = SlidingWindow(**params)

            if dimension is not None and self.dimension_ != dimension:
                msg = 'inconsistent "dimension" (is: {0}, should be: {1})'
                raise ValueError(msg.format(dimension, self.dimensions_))

            if ((sliding_window is not None) and
                ((sliding_window.start != self.sliding_window_.start) or
                 (sliding_window.duration != self.sliding_window_.duration) or
                 (sliding_window.step != self.sliding_window_.step))):
                msg = 'inconsistent "sliding_window"'
                raise ValueError(msg)

        else:

            if sliding_window is None:
                raise ValueError('missing "sliding_window" parameter.')
            if dimension is None:
                raise ValueError('missing "dimension" parameter.')

            # create parent directory
            mkdir_p(os.path.dirname(path))

            params = {'start': sliding_window.start,
                      'duration': sliding_window.duration,
                      'step': sliding_window.step,
                      'dimension': dimension}

            with io.open(path, 'w') as f:
                yaml.dump(params, f, default_flow_style=False)

            self.sliding_window_ = sliding_window
            self.dimension_ = dimension

    def sliding_window(self):
        return self.sliding_window_

    def dimension(self):
        return self.dimension_

    def __call__(self, item):

        path = self.get_path(item)

        if not os.path.exists(path):
            uri = get_unique_identifier(item)
            msg = 'No precomputed features for "{uri}".'
            raise PyannoteFeatureExtractionError(msg.format(uri=uri))

        if self.use_memmap:
            data = np.load(path, mmap_mode='r')
        else:
            data = np.load(path)

        if self.mu is None and self.sigma is None:
            return SlidingWindowFeature(data, self.sliding_window_)

        return SlidingWindowFeature((data - self.mu) / self.sigma,
                                    self.sliding_window_)

    def dump(self, item, features):
        path = self.get_path(item)
        mkdir_p(os.path.dirname(path))
        np.save(path, features.data)


class PrecomputedHTK(object):

    def __init__(self, root_dir=None, duration=0.025, step=None,
                 mu=0., sigma=1.):
        super(PrecomputedHTK, self).__init__()
        self.root_dir = root_dir
        self.duration = duration
        self.mu = mu
        self.sigma = sigma

        # load any htk file in root_dir/database
        path = '{root_dir}/*/*.htk'.format(root_dir=root_dir)
        found = glob(path)

        # FIXME switch to Py3.5 and use glob 'recursive' parameter
        # http://stackoverflow.com/questions/2186525/
        # use-a-glob-to-find-files-recursively-in-python

        if len(found) > 0:
            file_htk = found[0]
        else:
            msg = "Could not find any HTK file in '{root_dir}'."
            raise ValueError(msg.format(root_dir=root_dir))

        X, sample_period = self.load_htk(file_htk)
        self.dimension_ = X.shape[1]
        self.step = sample_period * 1e-7

        # don't trust HTK header when 'step' is provided by the user.
        # HACK remove this when Pepe's HTK files are fixed...
        if step is not None:
            self.step = step

        self.sliding_window_ = SlidingWindow(start=0.,
                                             duration=self.duration,
                                             step=self.step)

    def sliding_window(self):
        return self.sliding_window_

    def dimension(self):
        return self.dimension_

    @staticmethod
    def get_path(root_dir, item):
        uri = get_unique_identifier(item)
        path = '{root_dir}/{uri}.htk'.format(root_dir=root_dir, uri=uri)
        return path

    # http://codereview.stackexchange.com/questions/
    # 1496/reading-a-binary-file-containing-periodic-samples
    @staticmethod
    def load_htk(file_htk):
        with open(file_htk, 'rb') as fp:
            data = fp.read(12)
            num_samples, sample_period, sample_size, _ = unpack('>iihh', data)
            num_features = int(sample_size / 4)
            num_samples = int(num_samples)
            X = np.empty((num_samples, num_features))
            for i in range(num_samples):
                data = fp.read(sample_size)
                X[i, :] = unpack('>' + ('f' * (sample_size // 4)), data)
        return X, sample_period

    def __call__(self, item):
        file_htk = self.get_path(self.root_dir, item)
        X, _ = self.load_htk(file_htk)
        return SlidingWindowFeature((X - self.mu) / self.sigma,
                                    self.sliding_window_)
