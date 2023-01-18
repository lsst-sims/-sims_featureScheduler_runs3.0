#!/usr/bin/env python

import numpy as np
import matplotlib.pylab as plt
import healpy as hp
from rubin_sim.scheduler.model_observatory import ModelObservatory
from rubin_sim.scheduler.schedulers import CoreScheduler, FilterSchedUzy
from rubin_sim.scheduler.utils import (SkyAreaGeneratorGalplane, ConstantFootprint,
                                       make_rolling_footprints)
import rubin_sim.scheduler.basis_functions as bf
from rubin_sim.scheduler.surveys import (GreedySurvey, BlobSurvey, ScriptedSurvey)
from rubin_sim.scheduler import sim_runner
import rubin_sim.scheduler.detailers as detailers
import sys
import subprocess
import os
import argparse
from make_ddf_survey import generate_ddf_scheduled_obs
from astropy.coordinates import SkyCoord
from astropy import units as u
from rubin_sim.utils import _hpid2_ra_dec, angular_separation, gnomonic_project_toxy
import rubin_sim
from gen_long_gaps import gen_long_gaps_survey
# So things don't fail on hyak
from astropy.utils import iers
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon

iers.conf.auto_download = False


class EuclidOverlapFootprint(SkyAreaGeneratorGalplane):

    def add_euclid_overlap(self, filter_ratios, label='euclid_overlap',
                           contour_file='EWS.SGC.Mainland.ROI.2022.RADEC.txt',
                           south_limit=-50.):

        names = ['RA', 'dec']
        types = [float, float]
        euclid_contours = np.genfromtxt('EWS.SGC.Mainland.ROI.2022.RADEC.txt',
                                        dtype=list(zip(names, types)))

        wrap_ra = self.ra + 0
        wrap_ra[np.where(wrap_ra > 180)] -= 360

        polygon = Polygon(zip(euclid_contours['RA'], euclid_contours['dec']))
        in_poly = [polygon.contains(Point(x, y)) for x, y in zip(wrap_ra, self.dec)]

        # find which map points are inside the contour
        indx = np.where((np.array(in_poly) == True & (self.pix_labels == "")))
        self.pix_labels[indx] = label
        for filtername in filter_ratios:
            self.healmaps[filtername][indx] = filter_ratios[filtername]

    def return_maps(self,
        lmc_ra=89.0,
        lmc_dec=-70,
        magellenic_clouds_ratios={
            "u": 0.32,
            "g": 0.4,
            "r": 1.0,
            "i": 1.0,
            "z": 0.9,
            "y": 0.9,
        },
        low_dust_ratios={"u": 0.32, "g": 0.4, "r": 1.0, "i": 1.0, "z": 0.9, "y": 0.9},
        virgo_ratios={"u": 0.32, "g": 0.4, "r": 1.0, "i": 1.0, "z": 0.9, "y": 0.9},
        scp_ratios={"u": 0.08, "g": 0.15, "r": 0.08, "i": 0.15, "z": 0.08, "y": 0.06},
        nes_ratios={"g": 0.23, "r": 0.33, "i": 0.33, "z": 0.23},
        bulge_ratios={"u": 0.19, "g": 0.57, "r": 1.15, "i": 1.05, "z": 0.78, "y": 0.57},
        dusty_plane_ratios={
            "u": 0.07,
            "g": 0.13,
            "r": 0.28,
            "i": 0.28,
            "z": 0.25,
            "y": 0.18,
        },
        euclid_ratios={"u": 0.32, "g": 0.4, "r": 1.0, "i": 1.0, "z": 0.9, "y": 0.9},
    ):
        """
        Parameters:
        various_ratios : `dict`
            Dict with filternames for keys and floats for values that are the desired ratio
            of observations in each filter. By conventions, I usually set the low_dust_ratios['r']=1,
            then all the other values can be interpreted relative to that. E.g., if scp_ratios['u']=0.1, then
            when the low_dust r has 10 visits (per pixel) the scp should have 1 vist (per pixel).

        Returns
        --------
        self.healmaps : `np.ndarray`
            HEALPix maps for ugrizy
        self.pix_labels : `np.ndarray`
            Array string labels for each HEALpix
        """

        # Array to hold the labels for each pixel
        self.pix_labels = np.zeros(hp.nside2npix(self.nside), dtype="U20")
        self.healmaps = np.zeros(
            hp.nside2npix(self.nside),
            dtype=list(zip(["u", "g", "r", "i", "z", "y"], [float] * 7)),
        )

        # Note, order here matters. Once a HEALpix is set and labled, subsequent add_ methods
        # will not override that pixel.
        self.add_magellanic_clouds(
            magellenic_clouds_ratios, lmc_ra=lmc_ra, lmc_dec=lmc_dec
        )
        self.add_lowdust_wfd(low_dust_ratios)
        self.add_virgo_cluster(virgo_ratios)
        self.add_bulgy(bulge_ratios)
        self.add_nes(nes_ratios)
        self.add_dusty_plane(dusty_plane_ratios)
        self.add_euclid_overlap(euclid_ratios)
        self.add_scp(scp_ratios)

        return self.healmaps, self.pix_labels


def gen_GreedySurveys(nside=32, nexp=2, exptime=30., filters=['r', 'i', 'z', 'y'],
                      camera_rot_limits=[-80., 80.],
                      shadow_minutes=60., max_alt=76., moon_distance=30., ignore_obs=['DD', 'twilight_neo'],
                      m5_weight=3., footprint_weight=0.75, slewtime_weight=3.,
                      stayfilter_weight=3., repeat_weight=-1., footprints=None):
    """
    Make a quick set of greedy surveys

    This is a convienence function to generate a list of survey objects that can be used with
    rubin_sim.scheduler.schedulers.Core_scheduler.
    To ensure we are robust against changes in the sims_featureScheduler codebase, all kwargs are
    explicitly set.

    Parameters
    ----------
    nside : int (32)
        The HEALpix nside to use
    nexp : int (1)
        The number of exposures to use in a visit.
    exptime : float (30.)
        The exposure time to use per visit (seconds)
    filters : list of str (['r', 'i', 'z', 'y'])
        Which filters to generate surveys for.
    camera_rot_limits : list of float ([-80., 80.])
        The limits to impose when rotationally dithering the camera (degrees).
    shadow_minutes : float (60.)
        Used to mask regions around zenith (minutes)
    max_alt : float (76.
        The maximium altitude to use when masking zenith (degrees)
    moon_distance : float (30.)
        The mask radius to apply around the moon (degrees)
    ignore_obs : str or list of str ('DD')
        Ignore observations by surveys that include the given substring(s).
    m5_weight : float (3.)
        The weight for the 5-sigma depth difference basis function
    footprint_weight : float (0.3)
        The weight on the survey footprint basis function.
    slewtime_weight : float (3.)
        The weight on the slewtime basis function
    stayfilter_weight : float (3.)
        The weight on basis function that tries to stay avoid filter changes.
    """
    # Define the extra parameters that are used in the greedy survey. I
    # think these are fairly set, so no need to promote to utility func kwargs
    greed_survey_params = {'block_size': 1, 'smoothing_kernel': None,
                           'seed': 42, 'camera': 'LSST', 'dither': True,
                           'survey_name': 'greedy'}

    surveys = []
    detailer_list = [detailers.CameraRotDetailer(min_rot=np.min(camera_rot_limits), max_rot=np.max(camera_rot_limits))]
    detailer_list.append(detailers.Rottep2RotspDesiredDetailer())

    for filtername in filters:
        bfs = []
        bfs.append((bf.M5DiffBasisFunction(filtername=filtername, nside=nside), m5_weight))
        bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                              footprint=footprints,
                                              out_of_bounds_val=np.nan, nside=nside), footprint_weight))
        bfs.append((bf.SlewtimeBasisFunction(filtername=filtername, nside=nside), slewtime_weight))
        bfs.append((bf.StrictFilterBasisFunction(filtername=filtername), stayfilter_weight))
        bfs.append((bf.VisitRepeatBasisFunction(gap_min=0, gap_max=2*60., filtername=None,
                                                nside=nside, npairs=20), repeat_weight))
        # Masks, give these 0 weight
        bfs.append((bf.ZenithShadowMaskBasisFunction(nside=nside, shadow_minutes=shadow_minutes,
                                                     max_alt=max_alt), 0))
        bfs.append((bf.MoonAvoidanceBasisFunction(nside=nside, moon_distance=moon_distance), 0))

        bfs.append((bf.FilterLoadedBasisFunction(filternames=filtername), 0))
        bfs.append((bf.PlanetMaskBasisFunction(nside=nside), 0))

        weights = [val[1] for val in bfs]
        basis_functions = [val[0] for val in bfs]
        surveys.append(GreedySurvey(basis_functions, weights, exptime=exptime, filtername=filtername,
                                     nside=nside, ignore_obs=ignore_obs, nexp=nexp,
                                     detailers=detailer_list, **greed_survey_params))

    return surveys


def generate_blobs(nside, nexp=2, exptime=30., filter1s=['u', 'u', 'g', 'r', 'i', 'z', 'y'],
                   filter2s=['g', 'r', 'r', 'i', 'z', 'y', 'y'], pair_time=33.,
                   camera_rot_limits=[-80., 80.], n_obs_template=3,
                   season=300., season_start_hour=-4., season_end_hour=2.,
                   shadow_minutes=60., max_alt=76., moon_distance=30., ignore_obs=['DD', 'twilight_neo'],
                   m5_weight=6., footprint_weight=1.5, slewtime_weight=3.,
                   stayfilter_weight=3., template_weight=12., u_template_weight=24., footprints=None, u_nexp1=True,
                   scheduled_respect=45., good_seeing={'g': 3, 'r': 3, 'i': 3}, good_seeing_weight=3.,
                   mjd_start=1, repeat_weight=-20):
    """
    Generate surveys that take observations in blobs.

    Parameters
    ----------
    nside : int (32)
        The HEALpix nside to use
    nexp : int (1)
        The number of exposures to use in a visit.
    exptime : float (30.)
        The exposure time to use per visit (seconds)
    filter1s : list of str
        The filternames for the first set
    filter2s : list of str
        The filter names for the second in the pair (None if unpaired)
    pair_time : float (33)
        The ideal time between pairs (minutes)
    camera_rot_limits : list of float ([-80., 80.])
        The limits to impose when rotationally dithering the camera (degrees).
    n_obs_template : int (3)
        The number of observations to take every season in each filter
    season : float (300)
        The length of season (i.e., how long before templates expire) (days)
    season_start_hour : float (-4.)
        For weighting how strongly a template image needs to be observed (hours)
    sesason_end_hour : float (2.)
        For weighting how strongly a template image needs to be observed (hours)
    shadow_minutes : float (60.)
        Used to mask regions around zenith (minutes)
    max_alt : float (76.
        The maximium altitude to use when masking zenith (degrees)
    moon_distance : float (30.)
        The mask radius to apply around the moon (degrees)
    ignore_obs : str or list of str ('DD')
        Ignore observations by surveys that include the given substring(s).
    m5_weight : float (3.)
        The weight for the 5-sigma depth difference basis function
    footprint_weight : float (0.3)
        The weight on the survey footprint basis function.
    slewtime_weight : float (3.)
        The weight on the slewtime basis function
    stayfilter_weight : float (3.)
        The weight on basis function that tries to stay avoid filter changes.
    template_weight : float (12.)
        The weight to place on getting image templates every season
    u_template_weight : float (24.)
        The weight to place on getting image templates in u-band. Since there
        are so few u-visits, it can be helpful to turn this up a little higher than
        the standard template_weight kwarg.
    u_nexp1 : bool (True)
        Add a detailer to make sure the number of expossures in a visit is always 1 for u observations.
    scheduled_respect : float (45)
        How much time to require there be before a pre-scheduled observation (minutes)
    """

    template_weights = {'u': u_template_weight, 'g': template_weight,
                        'r': template_weight, 'i': template_weight,
                        'z': template_weight, 'y': template_weight}

    BlobSurvey_params = {'slew_approx': 7.5, 'filter_change_approx': 140.,
                          'read_approx': 2., 'min_pair_time': 15., 'search_radius': 30.,
                          'alt_max': 85., 'az_range': 90., 'flush_time': 30.,
                          'smoothing_kernel': None, 'nside': nside, 'seed': 42, 'dither': True,
                          'twilight_scale': False}

    surveys = []

    times_needed = [pair_time, pair_time*2]
    for filtername, filtername2 in zip(filter1s, filter2s):
        detailer_list = []
        detailer_list.append(detailers.CameraRotDetailer(min_rot=np.min(camera_rot_limits),
                                                         max_rot=np.max(camera_rot_limits)))
        detailer_list.append(detailers.Rottep2RotspDesiredDetailer())
        detailer_list.append(detailers.CloseAltDetailer())
        detailer_list.append(detailers.FlushForSchedDetailer())
        # List to hold tuples of (basis_function_object, weight)
        bfs = []

        if filtername2 is not None:
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername, nside=nside), m5_weight/2.))
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername2, nside=nside), m5_weight/2.))

        else:
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername, nside=nside), m5_weight))

        if filtername2 is not None:
            bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                                    footprint=footprints,
                                                    out_of_bounds_val=np.nan, nside=nside), footprint_weight/2.))
            bfs.append((bf.FootprintBasisFunction(filtername=filtername2,
                                                    footprint=footprints,
                                                    out_of_bounds_val=np.nan, nside=nside), footprint_weight/2.))
        else:
            bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                                    footprint=footprints,
                                                    out_of_bounds_val=np.nan, nside=nside), footprint_weight))

        bfs.append((bf.SlewtimeBasisFunction(filtername=filtername, nside=nside), slewtime_weight))
        bfs.append((bf.StrictFilterBasisFunction(filtername=filtername), stayfilter_weight))
        bfs.append((bf.VisitRepeatBasisFunction(gap_min=0, gap_max=3*60., filtername=None,
                                                   nside=nside, npairs=20), repeat_weight))

        if filtername2 is not None:
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername, nside=nside,
                                                         footprint=footprints.get_footprint(filtername),
                                                         n_obs=n_obs_template, season=season,
                                                         season_start_hour=season_start_hour,
                                                         season_end_hour=season_end_hour), template_weights[filtername]/2.))
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername2, nside=nside,
                                                         footprint=footprints.get_footprint(filtername2),
                                                         n_obs=n_obs_template, season=season,
                                                         season_start_hour=season_start_hour,
                                                         season_end_hour=season_end_hour), template_weights[filtername2]/2.))
        else:
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername, nside=nside,
                                                         footprint=footprints.get_footprint(filtername),
                                                         n_obs=n_obs_template, season=season,
                                                         season_start_hour=season_start_hour,
                                                         season_end_hour=season_end_hour), template_weight))

        # Insert things for getting good seeing templates
        if filtername2 is not None:
            if filtername in list(good_seeing.keys()):
                bfs.append((bf.NGoodSeeingBasisFunction(filtername=filtername, nside=nside, mjd_start=mjd_start,
                                                            footprint=footprints.get_footprint(filtername),
                                                            n_obs_desired=good_seeing[filtername]), good_seeing_weight))
            if filtername2 in list(good_seeing.keys()):
                bfs.append((bf.NGoodSeeingBasisFunction(filtername=filtername2, nside=nside, mjd_start=mjd_start,
                                                            footprint=footprints.get_footprint(filtername2),
                                                            n_obs_desired=good_seeing[filtername2]), good_seeing_weight))
        else:
            if filtername in list(good_seeing.keys()):
                bfs.append((bf.NGoodSeeingBasisFunction(filtername=filtername, nside=nside, mjd_start=mjd_start,
                                                            footprint=footprints.get_footprint(filtername),
                                                            n_obs_desired=good_seeing[filtername]), good_seeing_weight))
        # Make sure we respect scheduled observations
        bfs.append((bf.TimeToScheduledBasisFunction(time_needed=scheduled_respect), 0))
        # Masks, give these 0 weight
        bfs.append((bf.ZenithShadowMaskBasisFunction(nside=nside, shadow_minutes=shadow_minutes, max_alt=max_alt,
                                                         penalty=np.nan, site='LSST'), 0.))
        bfs.append((bf.MoonAvoidanceBasisFunction(nside=nside, moon_distance=moon_distance), 0.))
        filternames = [fn for fn in [filtername, filtername2] if fn is not None]
        bfs.append((bf.FilterLoadedBasisFunction(filternames=filternames), 0))
        if filtername2 is None:
            time_needed = times_needed[0]
        else:
            time_needed = times_needed[1]
        bfs.append((bf.TimeToTwilightBasisFunction(time_needed=time_needed), 0.))
        bfs.append((bf.NotTwilightBasisFunction(), 0.))
        bfs.append((bf.PlanetMaskBasisFunction(nside=nside), 0.))

        # unpack the basis functions and weights
        weights = [val[1] for val in bfs]
        basis_functions = [val[0] for val in bfs]
        if filtername2 is None:
            survey_name = 'blob, %s' % filtername
        else:
            survey_name = 'blob, %s%s' % (filtername, filtername2)
        if filtername2 is not None:
            detailer_list.append(detailers.TakeAsPairsDetailer(filtername=filtername2))

        if u_nexp1:
            detailer_list.append(detailers.FilterNexp(filtername='u', nexp=1))
        surveys.append(BlobSurvey(basis_functions, weights, filtername1=filtername, filtername2=filtername2,
                                   exptime=exptime,
                                   ideal_pair_time=pair_time,
                                   survey_note=survey_name, ignore_obs=ignore_obs,
                                   nexp=nexp, detailers=detailer_list, **BlobSurvey_params))

    return surveys


def generate_twi_blobs(nside, nexp=2, exptime=30., filter1s=['r', 'i', 'z', 'y'],
                       filter2s=['i', 'z', 'y', 'y'], pair_time=15.,
                       camera_rot_limits=[-80., 80.], n_obs_template=3,
                       season=300., season_start_hour=-4., season_end_hour=2.,
                       shadow_minutes=60., max_alt=76., moon_distance=30., ignore_obs=['DD', 'twilight_neo'],
                       m5_weight=6., footprint_weight=1.5, slewtime_weight=3.,
                       stayfilter_weight=3., template_weight=12., footprints=None, repeat_night_weight=None,
                       wfd_footprint=None, scheduled_respect=15., repeat_weight=-1.,
                       night_pattern=None):
    """
    Generate surveys that take observations in blobs.

    Parameters
    ----------
    nside : int (32)
        The HEALpix nside to use
    nexp : int (1)
        The number of exposures to use in a visit.
    exptime : float (30.)
        The exposure time to use per visit (seconds)
    filter1s : list of str
        The filternames for the first set
    filter2s : list of str
        The filter names for the second in the pair (None if unpaired)
    pair_time : float (22)
        The ideal time between pairs (minutes)
    camera_rot_limits : list of float ([-80., 80.])
        The limits to impose when rotationally dithering the camera (degrees).
    n_obs_template : int (3)
        The number of observations to take every season in each filter
    season : float (300)
        The length of season (i.e., how long before templates expire) (days)
    season_start_hour : float (-4.)
        For weighting how strongly a template image needs to be observed (hours)
    sesason_end_hour : float (2.)
        For weighting how strongly a template image needs to be observed (hours)
    shadow_minutes : float (60.)
        Used to mask regions around zenith (minutes)
    max_alt : float (76.
        The maximium altitude to use when masking zenith (degrees)
    moon_distance : float (30.)
        The mask radius to apply around the moon (degrees)
    ignore_obs : str or list of str ('DD')
        Ignore observations by surveys that include the given substring(s).
    m5_weight : float (3.)
        The weight for the 5-sigma depth difference basis function
    footprint_weight : float (0.3)
        The weight on the survey footprint basis function.
    slewtime_weight : float (3.)
        The weight on the slewtime basis function
    stayfilter_weight : float (3.)
        The weight on basis function that tries to stay avoid filter changes.
    template_weight : float (12.)
        The weight to place on getting image templates every season
    u_template_weight : float (24.)
        The weight to place on getting image templates in u-band. Since there
        are so few u-visits, it can be helpful to turn this up a little higher than
        the standard template_weight kwarg.
    """

    BlobSurvey_params = {'slew_approx': 7.5, 'filter_change_approx': 140.,
                          'read_approx': 2., 'min_pair_time': 10., 'search_radius': 30.,
                          'alt_max': 85., 'az_range': 90., 'flush_time': 30.,
                          'smoothing_kernel': None, 'nside': nside, 'seed': 42, 'dither': True,
                          'twilight_scale': False, 'in_twilight': True}

    surveys = []

    times_needed = [pair_time, pair_time*2]
    for filtername, filtername2 in zip(filter1s, filter2s):
        detailer_list = []
        detailer_list.append(detailers.CameraRotDetailer(min_rot=np.min(camera_rot_limits),
                                                         max_rot=np.max(camera_rot_limits)))
        detailer_list.append(detailers.Rottep2RotspDesiredDetailer())
        detailer_list.append(detailers.CloseAltDetailer())
        detailer_list.append(detailers.FlushForSchedDetailer())
        # List to hold tuples of (basis_function_object, weight)
        bfs = []

        if filtername2 is not None:
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername, nside=nside), m5_weight/2.))
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername2, nside=nside), m5_weight/2.))

        else:
            bfs.append((bf.M5DiffBasisFunction(filtername=filtername, nside=nside), m5_weight))

        if filtername2 is not None:
            bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                                  footprint=footprints,
                                                  out_of_bounds_val=np.nan, nside=nside), footprint_weight/2.))
            bfs.append((bf.FootprintBasisFunction(filtername=filtername2,
                                                  footprint=footprints,
                                                  out_of_bounds_val=np.nan, nside=nside), footprint_weight/2.))
        else:
            bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                                  footprint=footprints,
                                                  out_of_bounds_val=np.nan, nside=nside), footprint_weight))

        bfs.append((bf.SlewtimeBasisFunction(filtername=filtername, nside=nside), slewtime_weight))
        bfs.append((bf.StrictFilterBasisFunction(filtername=filtername), stayfilter_weight))
        bfs.append((bf.VisitRepeatBasisFunction(gap_min=0, gap_max=2*60., filtername=None,
                                                nside=nside, npairs=20), repeat_weight))

        if filtername2 is not None:
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername, nside=nside,
                                                    footprint=footprints.get_footprint(filtername),
                                                    n_obs=n_obs_template, season=season,
                                                    season_start_hour=season_start_hour,
                                                    season_end_hour=season_end_hour), template_weight/2.))
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername2, nside=nside,
                                                    footprint=footprints.get_footprint(filtername2),
                                                    n_obs=n_obs_template, season=season,
                                                    season_start_hour=season_start_hour,
                                                    season_end_hour=season_end_hour), template_weight/2.))
        else:
            bfs.append((bf.NObsPerYearBasisFunction(filtername=filtername, nside=nside,
                                                    footprint=footprints.get_footprint(filtername),
                                                    n_obs=n_obs_template, season=season,
                                                    season_start_hour=season_start_hour,
                                                    season_end_hour=season_end_hour), template_weight))
        if repeat_night_weight is not None:
            bfs.append((bf.AvoidLongGapsBasisFunction(nside=nside, filtername=None,
                                                          min_gap=0., max_gap=10./24., ha_limit=3.5,
                                                          footprint=wfd_footprint), repeat_night_weight))
        # Make sure we respect scheduled observations
        bfs.append((bf.TimeToScheduledBasisFunction(time_needed=scheduled_respect), 0))
        # Masks, give these 0 weight
        bfs.append((bf.ZenithShadowMaskBasisFunction(nside=nside, shadow_minutes=shadow_minutes, max_alt=max_alt,
                                                     penalty=np.nan, site='LSST'), 0.))
        bfs.append((bf.MoonAvoidanceBasisFunction(nside=nside, moon_distance=moon_distance), 0.))
        filternames = [fn for fn in [filtername, filtername2] if fn is not None]
        bfs.append((bf.FilterLoadedBasisFunction(filternames=filternames), 0))
        if filtername2 is None:
            time_needed = times_needed[0]
        else:
            time_needed = times_needed[1]
        bfs.append((bf.TimeToTwilightBasisFunction(time_needed=time_needed, alt_limit=12), 0.))
        bfs.append((bf.PlanetMaskBasisFunction(nside=nside), 0.))

        # Let's turn off twilight blobs on nights where we are 
        # doing NEO hunts
        bfs.append((bf.NightModuloBasisFunction(pattern=night_pattern), 0))

        # unpack the basis functions and weights
        weights = [val[1] for val in bfs]
        basis_functions = [val[0] for val in bfs]
        if filtername2 is None:
            survey_name = 'blob_twi, %s' % filtername
        else:
            survey_name = 'blob_twi, %s%s' % (filtername, filtername2)
        if filtername2 is not None:
            detailer_list.append(detailers.TakeAsPairsDetailer(filtername=filtername2))
        surveys.append(BlobSurvey(basis_functions, weights, filtername1=filtername, filtername2=filtername2,
                                   exptime=exptime,
                                   ideal_pair_time=pair_time,
                                   survey_note=survey_name, ignore_obs=ignore_obs,
                                   nexp=nexp, detailers=detailer_list, **BlobSurvey_params))

    return surveys


def ddf_surveys(detailers=None, season_frac=0.2, euclid_detailers=None):
    obs_array = generate_ddf_scheduled_obs(season_frac=season_frac)

    euclid_obs = np.where((obs_array['note'] == 'DD:EDFS_b') | (obs_array['note'] == 'DD:EDFS_a'))[0]
    all_other = np.where((obs_array['note'] != 'DD:EDFS_b') & (obs_array['note'] != 'DD:EDFS_a'))[0]

    survey1 = ScriptedSurvey([], detailers=detailers)
    survey1.set_script(obs_array[all_other])

    survey2 = ScriptedSurvey([], detailers=euclid_detailers)
    survey2.set_script(obs_array[euclid_obs])

    return [survey1, survey2]


def ecliptic_target(nside=32, dist_to_eclip=40., dec_max=30., mask=None):
    """Generate a target_map for the area around the ecliptic
    """

    ra, dec = _hpid2_ra_dec(nside, np.arange(hp.nside2npix(nside)))
    result = np.zeros(ra.size)
    coord = SkyCoord(ra=ra*u.rad, dec=dec*u.rad)
    eclip_lat = coord.barycentrictrueecliptic.lat.radian
    good = np.where((np.abs(eclip_lat) < np.radians(dist_to_eclip)) &
                    (dec < np.radians(dec_max)))
    result[good] += 1

    if mask is not None:
        result *= mask

    return result


def generate_twilight_neo(nside, night_pattern=None, nexp=1, exptime=15,
                          ideal_pair_time=5., max_airmass=2.,
                          camera_rot_limits=[-80., 80.],
                          time_needed=10, footprint_mask=None,
                          footprint_weight=0.1, slewtime_weight=3.,
                          stayfilter_weight=3., area_required=None,
                          filters='riz', n_repeat=3, sun_alt_limit=-14.8):
    # XXX finish eliminating magic numbers and document this one
    slew_estimate = 4.5
    survey_name = 'twilight_neo'
    footprint = ecliptic_target(nside=nside, mask=footprint_mask)
    constant_fp = ConstantFootprint()
    for filtername in filters:
        constant_fp.set_footprint(filtername, footprint)

    surveys = []
    for filtername in filters:
        detailer_list = []
        detailer_list.append(detailers.CameraRotDetailer(min_rot=np.min(camera_rot_limits), max_rot=np.max(camera_rot_limits)))
        detailer_list.append(detailers.CloseAltDetailer())
        # Should put in a detailer so things start at lowest altitude
        detailer_list.append(detailers.TwilightTripleDetailer(slew_estimate=slew_estimate, n_repeat=n_repeat))
        bfs = []

        bfs.append((bf.FootprintBasisFunction(filtername=filtername,
                                                footprint=constant_fp,
                                                out_of_bounds_val=np.nan,
                                                nside=nside), footprint_weight))

        bfs.append((bf.SlewtimeBasisFunction(filtername=filtername, nside=nside), slewtime_weight))
        bfs.append((bf.StrictFilterBasisFunction(filtername=filtername), stayfilter_weight))
        # Need a toward the sun, reward high airmass, with an airmass cutoff basis function.
        bfs.append((bf.NearSunTwilightBasisFunction(nside=nside, max_airmass=max_airmass), 0))
        bfs.append((bf.ZenithShadowMaskBasisFunction(nside=nside, shadow_minutes=60., max_alt=76.), 0))
        bfs.append((bf.MoonAvoidanceBasisFunction(nside=nside, moon_distance=30.), 0))
        bfs.append((bf.FilterLoadedBasisFunction(filternames=filtername), 0))
        bfs.append((bf.PlanetMaskBasisFunction(nside=nside), 0))
        bfs.append((bf.SolarElongationMaskBasisFunction(min_elong=0., max_elong=60., nside=nside), 0))

        #bfs.append((bf.Sun_alt_limit_basis_function(alt_limit=-15), 0))
        #bfs.append((bf.Time_in_twilight_basis_function(time_needed=time_needed), 0))
        bfs.append((bf.NightModuloBasisFunction(pattern=night_pattern), 0))
        # Do not attempt unless the sun is getting high
        bfs.append(((bf.SunAltHighLimitBasisFunction(alt_limit=sun_alt_limit)), 0))

        # unpack the basis functions and weights
        weights = [val[1] for val in bfs]
        basis_functions = [val[0] for val in bfs]

        # Set huge ideal pair time and use the detailer to cut down the list of observations to fit twilight?
        surveys.append(BlobSurvey(basis_functions, weights, filtername1=filtername, filtername2=None,
                                   ideal_pair_time=ideal_pair_time, nside=nside, exptime=exptime,
                                   survey_note=survey_name, ignore_obs=['DD', 'greedy', 'blob'], dither=True,
                                   nexp=nexp, detailers=detailer_list, az_range=180., twilight_scale=False,
                                   area_required=area_required))
    return surveys


def run_sched(surveys, survey_length=365.25, nside=32, fileroot='baseline_', verbose=False,
              extra_info=None, illum_limit=40.):
    years = np.round(survey_length/365.25)
    scheduler = CoreScheduler(surveys, nside=nside)
    n_visit_limit = None
    fs = FilterSchedUzy(illum_limit=illum_limit)
    observatory = ModelObservatory(nside=nside)
    observatory, scheduler, observations = sim_runner(observatory, scheduler,
                                                      survey_length=survey_length,
                                                      filename=fileroot+'%iyrs.db' % years,
                                                      delete_past=True, n_visit_limit=n_visit_limit,
                                                      verbose=verbose, extra_info=extra_info,
                                                      filter_scheduler=fs)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", dest='verbose', action='store_true')
    parser.set_defaults(verbose=False)
    parser.add_argument("--survey_length", type=float, default=365.25*10)
    parser.add_argument("--outDir", type=str, default="")
    parser.add_argument("--maxDither", type=float, default=0.7, help="Dither size for DDFs (deg)")
    parser.add_argument("--moon_illum_limit", type=float, default=40., help="illumination limit to remove u-band")
    parser.add_argument("--nexp", type=int, default=2)
    parser.add_argument("--rolling_nslice", type=int, default=2)
    parser.add_argument("--rolling_strength", type=float, default=0.9)
    parser.add_argument("--dbroot", type=str)
    parser.add_argument("--gsw", type=float, default=3.0, help="good seeing weight")
    parser.add_argument("--ddf_season_frac", type=float, default=0.2)
    parser.add_argument("--agg_level", type=str, default="1.5", help="Version of aggregation level map - either 1.5 or 2.0")
    parser.add_argument("--nights_off", type=int, default=6)
    parser.add_argument("--nights_delayed", type=int, default=-1)
    parser.add_argument("--neo_night_pattern", type=int, default=4)
    parser.add_argument("--neo_filters", type=str, default='riz')
    parser.add_argument("--neo_repeat", type=int, default=4)

    args = parser.parse_args()
    survey_length = args.survey_length  # Days
    outDir = args.outDir
    verbose = args.verbose
    max_dither = args.maxDither
    illum_limit = args.moon_illum_limit
    nexp = args.nexp
    nslice = args.rolling_nslice
    rolling_scale = args.rolling_strength
    dbroot = args.dbroot
    gsw = args.gsw
    nights_off = args.nights_off
    nights_delayed = args.nights_delayed
    neo_night_pattern = args.neo_night_pattern
    neo_filters = args.neo_filters
    neo_repeat = args.neo_repeat

    ddf_season_frac = args.ddf_season_frac

    nside = 32
    per_night = True  # Dither DDF per night

    camera_ddf_rot_limit = 75.

    extra_info = {}
    exec_command = ''
    for arg in sys.argv:
        exec_command += ' ' + arg
    extra_info['exec command'] = exec_command
    try:
        extra_info['git hash'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'])
    except subprocess.CalledProcessError:
        extra_info['git hash'] = 'Not in git repo'

    extra_info['file executed'] = os.path.realpath(__file__)
    try:
        rs_path = rubin_sim.__path__[0]
        hash_file = os.path.join(rs_path, '../', '.git/refs/heads/main')
        extra_info['rubin_sim git hash'] = subprocess.check_output(['cat', hash_file])
    except subprocess.CalledProcessError:
        pass

    # Use the filename of the script to name the output database
    if dbroot is None:
        fileroot = os.path.basename(sys.argv[0]).replace('.py', '') + '_'
    else:
        fileroot = dbroot + '_'
    file_end = 'v2.99_'

    pattern_dict = {1: [True], 2: [True, False], 3: [True, False, False],
                    4: [True, False, False, False],
                    # 4 on, 4 off
                    5: [True, True, True, True, False, False, False, False],
                    # 3 on 4 off
                    6: [True, True, True, False, False, False, False],
                    7: [True, True, False, False, False, False]}
    neo_night_pattern = pattern_dict[neo_night_pattern]
    reverse_neo_night_pattern = [not val for val in neo_night_pattern]

    # Modify the footprint
    sky = EuclidOverlapFootprint(nside=nside, smc_radius=4, lmc_radius=6)
    footprints_hp_array, labels = sky.return_maps()

    wfd_indx = np.where((labels == 'lowdust') | (labels == 'LMC_SMC') | (labels == 'virgo'))[0]
    wfd_footprint = footprints_hp_array['r']*0
    wfd_footprint[wfd_indx] = 1

    footprints_hp = {}
    for key in footprints_hp_array.dtype.names:
        footprints_hp[key] = footprints_hp_array[key]

    footprint_mask = footprints_hp['r']*0
    footprint_mask[np.where(footprints_hp['r'] > 0)] = 1

    repeat_night_weight = None

    observatory = ModelObservatory(nside=nside)
    conditions = observatory.return_conditions()

    footprints = make_rolling_footprints(fp_hp=footprints_hp, mjd_start=conditions.mjd_start,
                                         sun_ra_start=conditions.sun_ra_start, nslice=nslice,
                                         scale=rolling_scale,
                                         nside=nside, wfd_indx=wfd_indx, order_roll=1, n_cycles=4)

    gaps_night_pattern = [True] + [False]*nights_off

    long_gaps = gen_long_gaps_survey(nside=nside, footprints=footprints,
                                     night_pattern=gaps_night_pattern, nights_delayed=nights_delayed)

    # Set up the DDF surveys to dither
    u_detailer = detailers.FilterNexp(filtername='u', nexp=1)
    dither_detailer = detailers.DitherDetailer(per_night=per_night, max_dither=max_dither)
    details = [detailers.CameraRotDetailer(min_rot=-camera_ddf_rot_limit, max_rot=camera_ddf_rot_limit),
               dither_detailer, u_detailer, detailers.Rottep2RotspDesiredDetailer()]
    euclid_detailers = [detailers.CameraRotDetailer(min_rot=-camera_ddf_rot_limit, max_rot=camera_ddf_rot_limit),
                        detailers.EuclidDitherDetailer(), u_detailer, detailers.Rottep2RotspDesiredDetailer()]
    ddfs = ddf_surveys(detailers=details, season_frac=ddf_season_frac, euclid_detailers=euclid_detailers)

    greedy = gen_GreedySurveys(nside, nexp=nexp, footprints=footprints)
    neo = generate_twilight_neo(nside, night_pattern=neo_night_pattern,
                                filters=neo_filters, n_repeat=neo_repeat,
                                footprint_mask=footprint_mask)
    blobs = generate_blobs(nside, nexp=nexp, footprints=footprints, mjd_start=conditions.mjd_start, good_seeing_weight=gsw)
    twi_blobs = generate_twi_blobs(nside, nexp=nexp,
                                   footprints=footprints,
                                   wfd_footprint=wfd_footprint,
                                   repeat_night_weight=repeat_night_weight, night_pattern=reverse_neo_night_pattern)
    surveys = [ddfs, long_gaps, blobs, twi_blobs, neo, greedy]
    run_sched(surveys, survey_length=survey_length, verbose=verbose,
              fileroot=os.path.join(outDir, fileroot+file_end), extra_info=extra_info,
              nside=nside, illum_limit=illum_limit)
