#!/usr/bin/env python
"""
Baxter_the_builder regrasping node
Author: Henry Smith & Andrew Chan
"""
import numpy as np
import time

import rospy
from baxter_interface import gripper as baxter_gripper
import baxter_interface
import actionlib
import tf

import transformations as trans
from ee106b_lab1.srv import *
from std_msgs.msg import (
    UInt16,
)
import copy

 
from geometry_msgs.msg import (
    PoseStamped,
    Pose,
    Point,
    Quaternion,
)
from std_msgs.msg import Header
 
from baxter_core_msgs.srv import (
    SolvePositionIK,
    SolvePositionIKRequest,
)

PI = np.pi

class Builder:
    def __init__(self, limb):
        rospy.init_node('regrasping_' + limb)

        # Set up gripper
        self.gripper = baxter_gripper.Gripper(limb)
        self.gripper.calibrate()

        # Set up limb
        self.limb = baxter_interface.Limb(limb)
        self.limb_name = limb

        # Set up tf stuff
        self.parent_frame = 'base'
        self.listener = tf.TransformListener()

        # Set up IK solver
        self.ns = "ExternalTools/" + self.limb_name + "/PositionKinematicsNode/IKService"
        self.iksvc = rospy.ServiceProxy(self.ns, SolvePositionIK)

        self.rate = rospy.Rate(100)
        rospy.sleep(1.0)

    def move_to_point(self, position, orientation, threshold=0.008726646, timeout = 15.0):
        goal = PoseStamped()
        goal.header = Header(stamp=rospy.Time.now(), frame_id='base')

        print "\nMoving to:"
        print(position)
        print(orientation)
        #x, y, and z position
        goal.pose.position.x =  position[0]
        goal.pose.position.y =  position[1]
        goal.pose.position.z =  position[2]
        
        #Orientation as a quaternion
        goal.pose.orientation.x = orientation[0] 
        goal.pose.orientation.y = orientation[1]
        goal.pose.orientation.z = orientation[2]
        goal.pose.orientation.w = orientation[3]

        # Solves IK
        ikreq = SolvePositionIKRequest()
        ikreq.pose_stamp.append(goal)
        try:
            rospy.wait_for_service(self.ns, 5.0)
            resp = self.iksvc(ikreq)
        except (rospy.ServiceException, rospy.ROSException), e:
            rospy.logerr("Service call failed: %s" % (e,))
            return 1

        if (resp.isValid[0]):
            print("SUCCESS - Valid Joint Solution Found:")
            # Format solution into limb compatible dictionary
            limb_joints = dict(zip(resp.joints[0].name, resp.joints[0].position))
            # Moves to solution
            self.limb.move_to_joint_positions(limb_joints, threshold=threshold, timeout=timeout)
        else:
            print("INVALID POSE - No Valid Joint Solution Found.")

    def align_with_top(self, block, realign_with_base = False):
        if not self.listener.frameExists(block):
            print block + ' not found'
            exit(0)

        # Finds block position and orientation
        self.listener.waitForTransform(self.parent_frame, block, rospy.Time(), rospy.Duration(4.0))
        t = self.listener.getLatestCommonTime(self.parent_frame, block)
        b_pos, b_quat = self.listener.lookupTransform(self.parent_frame, block, t)
        if abs(1-abs(trans.quaternion_matrix(b_quat)[0,0])) < .01:
            on_side = False
        else: 
            on_side = True
        # Throws away original orientation
        new_quat = np.array([1,0,0,0])
        new_pos = b_pos + np.array([0,0,.1])

        # Moves to above brick, then rotates until alignment
        self.move_to_point(new_pos, new_quat, threshold = .005)
        velocities = self.limb.joint_velocities()
        cmd = copy.deepcopy(velocities)
        for joint in cmd.keys():
            cmd[joint] = 0
        cmd['right_w2'] = .5
        while not rospy.is_shutdown():
            self.limb.set_joint_velocities(cmd)
            self.listener.waitForTransform('right_gripper', block, rospy.Time(), rospy.Duration(4.0))
            t = self.listener.getLatestCommonTime('right_gripper', block)
            position, quaternion = self.listener.lookupTransform(block, 'right_gripper', t)
            rot = np.array(trans.quaternion_matrix(quaternion)[:3,:3])
            # This is very hacky, forgive me

            if on_side:
                count = 0
                for i in range(2):
                    for j in range(1,3):
                        if abs(abs(rot[i,j])-1) < .02:
                            count += 1
                if count >= 2:
                    break
            else:
                count = 0
                for i in range(3):
                    for j in range(3):
                        if abs(abs(rot[i,j])-1) < .03:
                            count += 1
                if count >= 3:
                    break

        # Picks up brick
        t = self.listener.getLatestCommonTime('base', 'right_gripper')
        position, quaternion = self.listener.lookupTransform('base', 'right_gripper', t)
        self.move_to_point(b_pos - np.array([0,0,.02]), quaternion, timeout = 2.0)
        self.gripper.close()

        # Keeps or throws away current orientation
        if realign_with_base:
            print('realigning')
            self.move_to_point(b_pos + np.array([0,0,.03]), new_quat)
        else:
            self.move_to_point(b_pos + np.array([0,0,.05]), quaternion)
        # Returns original position for reference
        return b_pos

    def stack_bricks(self, b1, b2):
        if not self.listener.frameExists(b2):
            print 'block not found'
            exit(0)
        self.align_with_top(b1)

        # Moves to above next brick
        self.listener.waitForTransform(self.parent_frame, b2, rospy.Time(), rospy.Duration(4.0))
        t = self.listener.getLatestCommonTime(self.parent_frame, b2)
        b_pos, b_quat = self.listener.lookupTransform(self.parent_frame, b2, t)
        new_quat = np.array([1,0,0,0])
        new_pos = b_pos + np.array([0,0,.2])
        self.move_to_point(new_pos, new_quat)

        # Rotates until aligned
        velocities = self.limb.joint_velocities()
        cmd = copy.deepcopy(velocities)
        for joint in cmd.keys():
            cmd[joint] = 0
        cmd['right_w2'] = .5
        while not rospy.is_shutdown():
            self.limb.set_joint_velocities(cmd)
            t = self.listener.getLatestCommonTime(self.parent_frame, b2)
            position, quaternion = self.listener.lookupTransform(b2, 'right_gripper', t)
            rot = np.array(trans.quaternion_matrix(quaternion)[:3,:3])
            count = 0
            for i in range(3):
                for j in range(3):
                    if abs(abs(rot[i,j])-1) < .03:
                        count += 1
            if count >= 3:
                break

        # Stacks 'em
        self.move_to_point(b_pos + np.array([0,0,.055]), quaternion)
        print("Move Succesful")
        self.gripper.open()

        self.move_to_point(b_pos + np.array([0,0,.09]), quaternion)
        return b_pos


if __name__ == '__main__':
    # For recording Trajectory
    rospy.wait_for_service('endpoint_info')
    endpointSave = rospy.ServiceProxy('endpoint_save', endpoint_save)
    endpointInfo = rospy.ServiceProxy('endpoint_info', endpoint_service)
    endpointLoad = rospy.ServiceProxy('endpoint_load', endpoint_load)

    right_regrasper = Regrasper('right')
    # right_regrasper.flip('block_0')
    # right_regrasper.flip('block_0')
    # right_regrasper.flip('block_0')
    # right_regrasper.flip('block_0')
    #right_regrasper.stack_bricks('block_0', 'block_1')
    #right_regrasper.adjust_yaw('block_0')
    right_regrasper.flip('block_0')    
    right_regrasper.stack_bricks('block_0', 'block_1')

