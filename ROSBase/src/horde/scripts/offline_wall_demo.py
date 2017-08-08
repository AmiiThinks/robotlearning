from geometry_msgs.msg import Twist, Vector3
import numpy as np
import os
import rosbag

from cv_bridge.core import CvBridge
from gtd import GTD
from gvf import GVF
from state_representation import StateConstants, StateManager
from wall_demo import PavlovSoftmax, GoForward

def action_twist_to_binary(twist_action):
    action = np.zeros(action_space.size)
    if (action is not None):
        if (twist_action.angular.x > 0.0001):
            # accounts for turn
            action[1] = 1
        else:
            # accoutns for forward movement
            action[0] = 1
    return action

def get_state(entry, prev_entry):
    data = {'ir': None, 'imu': None, 'odom': None, 'charging':None}

    data['bump'] = [bool(bump) for bump in [entry.get('bump0'),
                                            entry.get('bump1'),
                                            entry.get('bump2')]]

    data['bias'] = True
    
    if entry.get('image') is not None:
        data['image'] = np.asarray(CvBridge().compressed_imgmsg_to_cv2(entry['image']))

    phi = offwd_state_manager.get_phi(**data)

    phi = np.concatenate([phi, action_twist_to_binary(last_twist)])
    observations = offwd_state_manager.get_observations(**data)

    return phi, observations

if __name__ == "__main__":
    time_scale = 0.1
    forward_speed = 0.2
    turn_speed = 1

    # all available actions
    action_space = np.array([Twist(Vector3(forward_speed, 0, 0),
                             Vector3(0, 0, 0)),
                             Twist(Vector3(0, 0, 0),
                             Vector3(0, 0, turn_speed))])
    features_to_use = ['image', 'bias']
    feature_indices = np.concatenate([StateConstants.indices_in_phi[f] for f in features_to_use])
    num_active_features = sum(StateConstants.num_active_features[f] for f in features_to_use)
    num_features = feature_indices.size
    discount = 1 - time_scale
    discount_if_bump = lambda obs: 0 if sum(obs["bump"]) else discount
    one_if_bump = lambda obs: int(any(obs['bump'])) if obs is not None else 0

    alpha0 = 0.05
    lmbda = 0.9
    dtb_hp = {'alpha': alpha0 / num_active_features,
              'beta': 0.001 * alpha0 / num_active_features,
              'lmbda': lmbda,
              'alpha0': alpha0,
              'num_features': num_features,
              'feature_indices': feature_indices,
             }

    # prediction GVF
    dtb_policy = GoForward(action_space=action_space)
    dtb_learner = GTD(**dtb_hp)

    threshold_policy = PavlovSoftmax(action_space=action_space,
                                     feature_indices=dtb_hp['feature_indices'],
                                     value_function=dtb_learner.predict,
                                     time_scale=time_scale)
    distance_to_bump = GVF(cumulant = one_if_bump,
                           gamma    = discount_if_bump,
                           target_policy = dtb_policy,
                           learner = dtb_learner,
                           name = 'DistanceToBump',
                           logger = None,
                           **dtb_hp)

    if not os.path.isfile('results.bag'):
        print("The required bag is not present")
        raise RuntimeError

    bag = rosbag.Bag('results.bag')


    # organize the bag data by clumping all data from a single timestep
    # in a dictionary
    last_t = None
    collected_history = []
    time_step_info = {}
    for topic, msg, t in bag.read_messages(topics=['bump0', 'bump1', 
                                                   'bump2', 'image',
                                                   'action']):
        if t != last_t and last_t is not None:
            collected_history.append(time_step_info)
        else:
            time_step_info[topic] = msg

        last_t = t

    # process and update data for the given learning algorithm
    offwd_state_manager = StateManager(features_to_use)
    prev_entry = None
    prev_phi = None
    prev_obs = None
    prev_mu = 1

    for entry in collected_history:
        phi, observations = get_state(entry, prev_entry)

        print phi

        prev_phi = phi
        prev_obs = observations
        prev_entry = entry