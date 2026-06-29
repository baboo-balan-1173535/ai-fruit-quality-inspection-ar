import cv2
import numpy as np

def nothing(x): pass

cap = cv2.VideoCapture(0)
cv2.namedWindow("Calibrate")
cv2.createTrackbar("H Low",  "Calibrate", 10, 179, nothing)
cv2.createTrackbar("H High", "Calibrate", 35, 179, nothing)
cv2.createTrackbar("S Low",  "Calibrate", 30, 255, nothing)
cv2.createTrackbar("S High", "Calibrate", 255, 255, nothing)
cv2.createTrackbar("V Low",  "Calibrate", 30, 255, nothing)
cv2.createTrackbar("V High", "Calibrate", 180, 255, nothing)

while True:
    ret, frame = cap.read()
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    hl = cv2.getTrackbarPos("H Low",  "Calibrate")
    hh = cv2.getTrackbarPos("H High", "Calibrate")
    sl = cv2.getTrackbarPos("S Low",  "Calibrate")
    sh = cv2.getTrackbarPos("S High", "Calibrate")
    vl = cv2.getTrackbarPos("V Low",  "Calibrate")
    vh = cv2.getTrackbarPos("V High", "Calibrate")

    mask = cv2.inRange(hsv, np.array([hl,sl,vl]), np.array([hh,sh,vh]))
    result = cv2.bitwise_and(frame, frame, mask=mask)

    cv2.imshow("Original", frame)
    cv2.imshow("Mask", mask)
    cv2.imshow("Calibrate", result)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        print(f"Your values: lower=({hl},{sl},{vl}), upper=({hh},{sh},{vh})")
        break

cap.release()
cv2.destroyAllWindows()