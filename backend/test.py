class Solution(object):
    def findMedianSortedArrays(self, nums1, nums2):
        """
        :type nums1: List[int]
        :type nums2: List[int]
        :rtype: float
        """
        num3 = []
        a = 0
        b = 0
        for i in range(len(nums1) + len(nums2)):
            if a<len(nums1) and b==len(nums2):
                num3 += nums1[a:]
                break
            if a==len(nums1) and b<len(nums2):
                num3 += nums2[b:]
                break
            if nums1[a] > nums2[b]:
                num3.append(nums2[b])
                b += 1
            else:
                num3.append(nums1[a])
                a += 1
        medium = int(len(num3) / 2)
        print(medium)
        if len(num3) % 2 == 0:
            return (num3[medium] + num3[medium + 1])/2.0
        else:
            return num3[medium]
nums1 = [1,2]
nums2 = [3,4]
result = Solution().findMedianSortedArrays(nums1, nums2)
print(result)
