Based on more_z : change the filter swapping scheduler to sometimes swap u for y instead of always swapping u and z.
Turn rolling cadence down (0.5) and flip order (solar system). 
Add more weight to suppress repeats. 
Allow NEO twilight to run morning and evening. 

The DDF is still at 7.5% and the twilight NEO goes up to twi_neo_brightest_repeat4_riz_np4 parameters (and number of visits for twilight NEO). 
The galactic plane is still in the 'connected' footprint, but the filter balance was changed a bit to add more g and less y.
The SCP filter balance is tilted toward g and i, with less u,r,z, and y (g,i higher at 0.15; urz slightly lower at 0.08 instead of 0.1, y 0.06 instead of 0.1). 
