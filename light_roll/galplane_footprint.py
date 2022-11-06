import os
import numpy as np
import healpy as hp
from astropy.io import fits

from rubin_sim.scheduler.utils import Sky_area_generator

# sky = Sky_area_generator_galplane(nside=nside, smc_radius=4, lmc_radius=6)
# maps, labels = sky.return_maps(aggregation_level)

class Sky_area_generator_galplane(Sky_area_generator):
    def read_galplane_footprint(self, aggregation_level, root_dir="."):
        #  https://github.com/LSST-TVSSC/software_tools is original source for the files here
        # specifically, tvs_software_tools/GalPlaneSurvey/HighCadenceZone
        if isinstance(aggregation_level, float):
            aggregation_level = f"{aggregation_level :.1f}"
        if aggregation_level == "1.5":
            filename = "aggregated_priority_map_p1.5_a1.5_sum.fits"
        elif aggregation_level == "2.0":
            filename = "aggregated_priority_map_p2.0_a2.0_sum.fits"
        else:
            raise ValueError(
                f"Looking for either 1.5 or 2.0 (str) for aggregation_level, "
                f"not {aggregation_level}"
            )
        with fits.open(os.path.join(root_dir, filename)) as hdu1:
            self.gp = hdu1[1].data["pixelPriority"]
        # Turn gp map into mask for locations of high priority fields and resample
        self.gp_mask = np.where(self.gp > 0, 1, 0)
        self.gp_mask = hp.ud_grade(self.gp_mask, self.nside)

    def return_maps(
        self,
        aggregation_level=2.0,
        root_dir = ".",
        magellenic_clouds_ratios={
            "u": 0.32,
            "g": 0.4,
            "r": 1.0,
            "i": 1.0,
            "z": 0.9,
            "y": 0.9,
        },
        scp_ratios={"u": 0.1, "g": 0.1, "r": 0.1, "i": 0.1, "z": 0.1, "y": 0.1},
        nes_ratios={"g": 0.28, "r": 0.4, "i": 0.4, "z": 0.28},
        dusty_plane_ratios={'u': 0.19, 'g': 0.24, 'r': 0.24, 'i': 0.24, 'z': 0.24, 'y': 0.19},
        low_dust_ratios={"u": 0.32, "g": 0.4, "r": 1.0, "i": 1.0, "z": 0.9, "y": 0.9},
        bulge_ratios={'u': 0.56, 'g': 0.83, 'r': 0.95, 'i': 0.9, 'z': 0.71, 'y': 0.56},
        virgo_ratios={"u": 0.32, "g": 0.4, "r": 1.0, "i": 1.0, "z": 0.9, "y": 0.9},
    ):

        self.pix_labels = np.zeros(hp.nside2npix(self.nside), dtype="U20")
        self.healmaps = np.zeros(
            hp.nside2npix(self.nside),
            dtype=list(zip(["u", "g", "r", "i", "z", "y"], [float] * 7)),
        )
        self.add_magellanic_clouds(
            magellenic_clouds_ratios, lmc_ra=89, lmc_dec=-70
        )
        self.add_lowdust_wfd(low_dust_ratios)
        self.add_virgo_cluster(virgo_ratios)

        # Add galplane high priority regions
        self.read_galplane_footprint(aggregation_level, root_dir)
        label = "bulge"
        indx = np.where((self.gp_mask > 0) & (self.pix_labels == ""))
        self.pix_labels[indx] = label
        for filtername in bulge_ratios:
            self.healmaps[filtername][indx] = bulge_ratios[filtername]

        # self.add_bulge(bulge_ratios)
        self.add_nes(nes_ratios)
        self.add_dusty_plane(dusty_plane_ratios)
        self.add_scp(scp_ratios)

        return self.healmaps, self.pix_labels

