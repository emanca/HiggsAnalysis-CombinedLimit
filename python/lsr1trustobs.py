import numpy as np
import tensorflow as tf
import math

class SR1TrustExact:
    
  def __init__(self, loss, var,grad, initialtrustradius = 1.):
    
    self.trustradius = tf.Variable(initialtrustradius*tf.ones_like(loss),trainable=False)
    self.loss_old = tf.Variable(tf.zeros_like(loss), trainable=False)
    self.predicted_reduction = tf.Variable(tf.zeros_like(loss), trainable = False)
    self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    self.atboundary_old = tf.Variable(False, trainable=False)
    self.doiter_old = tf.Variable(False, trainable = False)
    self.grad_old = tf.Variable(tf.zeros_like(var), trainable=False)
    self.isfirstiter = tf.Variable(True, trainable=False)
    self.U = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    self.e = tf.Variable(tf.ones_like(var),trainable=False)
    self.doscaling = tf.Variable(False)
    
  def initialize(self, loss, var, grad, B = None):
    alist = []
    alist.append(tf.assign(self.var_old,var))
    alist.append(tf.assign(self.grad_old,grad))
    
    if B is not None:
      e,U = tf.self_adjoint_eig(B)
      alist.append(tf.assign(self.e,e))
      alist.append(tf.assign(self.U,U))
    return tf.group(alist)
  
  def minimize(self, loss, var, grad = None):
    
    if grad is None:
      grad = tf.gradients(loss,var, gate_gradients=True)[0]
    
    xtol = np.finfo(var.dtype.as_numpy_dtype).eps
    #eta = 0.
    eta = 0.15
    #eta = 1e-3
          
    actual_reduction = self.loss_old - loss
    
    #actual_reduction = tf.Print(actual_reduction,[self.loss_old, loss, actual_reduction])
    isnull = tf.logical_not(self.doiter_old)
    rho = actual_reduction/self.predicted_reduction
    rho = tf.where(tf.is_nan(loss), tf.zeros_like(loss), rho)
    rho = tf.where(isnull, tf.ones_like(loss), rho)
  
    dgrad = grad - self.grad_old
    dx = var - self.var_old
    dxmag = tf.sqrt(tf.reduce_sum(tf.square(dx)))
  
    trustradius_out = tf.where(tf.less(rho,0.25),0.25*self.trustradius,tf.where(tf.logical_and(tf.greater(rho,0.75),self.atboundary_old),2.*self.trustradius, self.trustradius))
    #trustradius_out = tf.minimum(trustradius_out,1e10)
    
    #trustradius_out = tf.where(tf.less(rho,0.1),0.5*self.trustradius,
                               #tf.where(tf.less(rho,0.75), self.trustradius,
                               #tf.where(tf.less_equal(dxmag,0.8*self.trustradius), self.trustradius,
                               #2.*self.trustradius)))
                               
    trustradius_out = tf.where(self.doiter_old, trustradius_out, self.trustradius)

    
    trustradius_out = tf.Print(trustradius_out, [actual_reduction,self.predicted_reduction,rho, trustradius_out], message = "actual_reduction, self.predicted_reduction, rho, trustradius_out: ")
    
    def doSR1Scaling(Bin,yin,dxin):
      s_norm2 = tf.reduce_sum(tf.square(dxin))
      y_norm2 = tf.reduce_sum(tf.square(yin))
      ys = tf.abs(tf.reduce_sum(yin*dxin))
      invalid = tf.equal(ys,0.) | tf.equal(y_norm2, 0.) | tf.equal(s_norm2, 0.)
      scale = tf.where(invalid, tf.ones_like(ys), y_norm2/ys)
      scale = tf.Print(scale,[scale],message = "doing sr1 scaling")
      B = scale*Bin
      return (B,H,tf.constant(False))
    
    #n.b. this has a substantially different form from the usual SR 1 update
    #since we are directly updating the eigenvalue-eigenvector decomposition.
    #The actual hessian approximation is never stored (but memory requirements
    #are similar since the full set of eigenvectors is stored)
    def doSR1Update(ein,Uin,yin,dxin):
      y = tf.reshape(yin,[-1,1])
      dx = tf.reshape(dxin,[-1,1])
      ecol = tf.reshape(ein,[-1,1])
      
      UTdx = tf.matmul(Uin, dx,transpose_a=True)
      UTy = tf.matmul(Uin,y,transpose_a=True)
      den = tf.matmul(y,dx,transpose_a=True) - tf.matmul(UTdx,ecol*UTdx,transpose_a=True)
      dyBx =  UTy - ecol*UTdx
      dyBxnormsq = tf.reduce_sum(tf.square(dyBx))
      dyBxnorm = tf.sqrt(dyBxnormsq)
      dxnorm = tf.sqrt(tf.reduce_sum(tf.square(dx)))
      dennorm = dxnorm*dyBxnorm
      dentest = tf.less(tf.abs(den),1e-8*dennorm)
      dentest = tf.reshape(dentest,[])
      dentest = tf.logical_or(dentest,tf.equal(actual_reduction,0.))
      
      
      def doUpdate():
        UTin = tf.transpose(Uin)
        z = dyBx/dyBxnorm
        signedrho = dyBxnormsq/den
        signedrho = tf.reshape(signedrho,[])
        signedrho = tf.Print(signedrho,[signedrho],message="signedrho")
        rho = tf.abs(signedrho)

        enorm = ein/signedrho

        flipsign = signedrho < 0.
        
        #in case rho<0, reverse order of eigenvalues and eigenvectors and flip signs
        #to ensure consistent ordering
        #z needs to be reversed as well since it was already computed with the original ordering
        einalt = -tf.reverse(ein,axis=(0,))
        enormalt = tf.reverse(enorm,axis=(0,))
        UTinalt = tf.reverse(UTin,axis=(0,))
        zalt = tf.reverse(z,axis=(0,))
        
        estart = tf.where(flipsign,einalt,ein)
        enormstart = tf.where(flipsign,enormalt,enorm)
        UTstart = tf.where(flipsign,UTinalt,UTin)
        z = tf.where(flipsign,zalt,z)
        
        #deflation in case of repeated eigenvalues
        unique, uniqueidxs, uniquecounts = tf.unique_with_counts(estart)
        #these are the indices of the eigenvalue-eigenvector pairs which are expected to change
        firstidxs = tf.cumsum(uniquecounts, exclusive=True)
        
        lastidxs = firstidxs + uniquecounts - 1
        lastidxscol = tf.reshape(lastidxs,[-1,1])
        
        islast = tf.scatter_nd(lastidxscol,tf.ones_like(unique,dtype=tf.bool),estart.shape)
        nonlastidxscol = tf.where(tf.logical_not(islast))
        nonlastidxs = tf.reshape(nonlastidxscol,[-1])
        
        #TODO, properly deflate for also xisq = 0 case
        
        zflat = tf.reshape(z,[-1])
        xisq = tf.square(z)
        #xisq2 = tf.segment_sum(xisq,uniqueidxs)
        xisq2 = tf.unsorted_segment_sum(xisq,uniqueidxs,tf.shape(unique)[0])
        absz2 = tf.sqrt(xisq2)
        z2 = -absz2
        
        #TODO Consider also moving the splitting of repeating vs nonrepeating
        #eigenvalues outside of the deflation
        
        arr0 = tf.TensorArray(var.dtype,size=tf.shape(unique)[0],infer_shape=False,element_shape=[None,var.shape[0]])
        deflate_var_list = [arr0, tf.constant(0,dtype=tf.int32)]
        def deflate_cond(arr,j):
          return j<arr.size()
        def deflate_body(arr,j):
          size = uniquecounts[j]
          startidx = firstidxs[j]
          endidx = startidx + size
          zsub = zflat[startidx:endidx]
          UTsub = UTstart[startidx:endidx]
          magzsub = absz2[j]
          en = tf.one_hot(size-1,depth=size,dtype=zsub.dtype)
          #this is the vector which implicitly defines the Householder transformation matrix
          v = zsub/magzsub + en
          v = v/tf.sqrt(tf.reduce_sum(tf.square(v)))
          v = tf.reshape(v,[-1,1])
          #protection for v~=0 case (when zsub~=-en), then no transformation is needed
          nullv = tf.reduce_all(tf.equal(tf.sign(zsub),-en))
          v = tf.where(nullv,tf.zeros_like(v),v)
          UTbarsub = UTsub - 2.*tf.matmul(v,tf.matmul(v,UTsub,transpose_a=True))
          arr = arr.write(j,UTbarsub)
          return (arr, j+1)
        
        UTbararr,j = tf.while_loop(deflate_cond,deflate_body,deflate_var_list, parallel_iterations=64, back_prop=False)
        UTbar = UTbararr.concat()
        
        #TODO, properly include xi^2 != 0 condition for building of deflated problem
        
        UT1 = tf.gather(UTbar,nonlastidxs)     
        UT2 = tf.gather(UTbar,lastidxs)
            
        
        unique = tf.Print(unique,[unique],message="unique")
        
        d = unique
        
        xisq = xisq2
        
        en1 = d[:-1]
        e1 = d[1:]
        delta = (e1 - en1)/rho
        delta = tf.Print(delta,[delta],message="delta",summarize=100000)
        xisqn1 = tf.reshape(xisq[:-1],[-1])
        xisq1 = tf.reshape(xisq[1:],[-1])
        xisqn = tf.reshape(xisq[-1],[])
        
        di = tf.reshape(d,[-1,1])
        dj = tf.reshape(d,[1,-1])
        deltam = (dj-di)/rho
        #deltamn1 = deltam[:-1]

        #s0mden = deltamn1 - tf.reshape(delta,[-1,1])
        #s0m = tf.reshape(xisq,[1,-1])/s0mden
        ##s0m = tf.where(tf.equal(s0mden,0.),tf.zeros_like(s0m),s0m)
        #s0m = tf.Print(s0m,[tf.shape(s0m)],message="s0mshape")
        ##protection for case where there is only one unique eigenvalue such that 
        ##this matrix is empty
        ##nlower = tf.minimum(1,tf.shape(s0m)[0])
        ##nlower = 1
        #nupper = tf.minimum(1,tf.shape(s0m)[0])
        ##s0mask = tf.matrix_band_part(tf.ones_like(s0m,dtype=tf.bool),nlower,0)
        #s0mask = tf.matrix_band_part(tf.ones_like(s0m,dtype=tf.bool),tf.zeros_like(nupper),nupper)
        #s0m = tf.where(s0mask,tf.zeros_like(s0m),s0m)
        ##s0m = s0m - tf.matrix_band_part(s0m,nlower,0)
        ##s0m = tf.Print(s0m,[s0mask],message="s0mask",summarize=10000)
        ##s0m = tf.Print(s0m,[s0m],message="s0m",summarize=10000)
        #s0 = tf.reduce_sum(s0m,axis=-1)
        
        #a0 = 1.+s0
        #b0 = -(xisqn1 + xisq1 + (1.+s0)*delta)
        #c0 = xisqn1*delta
        #t0n1 = (-b0 - tf.sqrt(tf.square(b0) - 4.*a0*c0))/(2*a0)
        ##t0n1 = tf.where(tf.is_nan(t0n1),tf.zeros_like(t0n1),t0n1)
        
        #t0n = tf.reshape(xisq[-1],[-1])
        #t0 = tf.concat([t0n1,t0n],axis=0)          
        
        #dmt0n1 = delta - t0n1
        
        #t0 = tf.zeros_like(d)
        
        t0n1 = tf.zeros_like(delta)
        t0n = tf.reshape(xisq[-1],[-1])
        t0 = tf.concat([t0n1,t0n],axis=0)
        
        t0n = tf.Print(t0n,[t0n],message="t0n")
        
        #t0 = tf.Print(t0,[dmt0n1],message="dmt0n1",summarize=10000)

        
        #t0 = tf.Print(t0,[s0],message="s0",summarize=10000)
        #t0 = tf.Print(t0,[a0],message="a0",summarize=10000)
        #t0 = tf.Print(t0,[b0],message="b0",summarize=10000)
        #t0 = tf.Print(t0,[c0],message="c0",summarize=10000)
        #t0 = tf.Print(t0,[t0],message="t0",summarize=10000)
                  
        #TODO, consider moving to more robust algorithm to find roots s.t. starting position of 0 and/or delta is valid
        #Alternatively fix the choice of starting point to avoid this problem (which occurs when xi^2_i and/or xi^2_{i+1}
        #are much smaller than delta)
                  
        nupper = tf.minimum(1,tf.shape(d)[0]-1)
        deltamask = tf.matrix_band_part(tf.ones_like(deltam,dtype=tf.bool),tf.zeros_like(nupper),nupper)
        #deltamask = tf.matrix_band_part(tf.ones_like(deltam,dtype=tf.bool),nupper,tf.zeros_like(nupper))

                  
        unconverged0 = tf.ones_like(t0,dtype=tf.bool)
                  
        loop_vars = [t0,unconverged0,tf.constant(0)]
        def cond(t,unconverged,j):
          return tf.reduce_any(unconverged) & (j<50)
        
        def body(t,unconverged,j):
          frden = tf.reciprocal(deltam - tf.reshape(t,[-1,1]))
          #exclude j=i and j=i+1 terms
          frden = tf.where(deltamask,tf.zeros_like(frden),frden)
          xisqj = tf.reshape(xisq,[1,-1])
          s0arg = xisqj*frden
          s1arg = s0arg*frden
          s2arg = s1arg*frden
          
          s0 = tf.reduce_sum(s0arg, axis=-1)
          s1 = tf.reduce_sum(s1arg, axis=-1)
          
          t2 = tf.square(t)
          
          tn1 = t[:-1]
          t2n1 = t2[:-1]
          t3n1 = t2n1*tn1
          
          dtn1 = delta-tn1
          dt2n1 = tf.square(dtn1)
          dt3n1 = dt2n1*dtn1
          
          s0n1 = s0[:-1]
          s1n1 = s1[:-1]
          s2n1 = tf.reduce_sum(s2arg[:-1], axis=-1)
          
          tn = t[-1]
          t2n = t2[-1]
          s0n = s0[-1]
          s1n = s1[-1]
          
          fn1 = 1. + s0n1 - xisqn1/tn1 + xisq1/dtn1
          fn = 1. + s0n - xisqn/tn
          fn = tf.reshape(fn,[-1])
          f = tf.concat([fn1,fn],axis=0)
          
          magw = tf.sqrt(tf.reduce_sum(tf.square(f)))
          
          #s2n1 = tf.Print(s2n1,[tf.shape(s2n1)],message="shape s2n1")
          #s2n1 = tf.Print(s2n1,[tf.shape(tn1)],message="shape tn1")
          #s2n1 = tf.Print(s2n1,[tf.shape(dt3n1)],message="shape dt3n1")
          #s2n1 = tf.Print(s2n1,[tf.shape(s1n1)],message="shape s1n1")
          #s2n1 = tf.Print(s2n1,[tf.shape(dt3n1)],message="shape dt3n1")
          #s2n1 = tf.Print(s2n1,[tf.shape(xisq1)],message="shape xisq1")
          #s2n1 = tf.Print(s2n1,[tf.shape(delta)],message="shape delta")
          
          cn1 = (s2n1*tn1*dt3n1 + s1n1*dt3n1 + xisq1*delta)/delta
          bn1 = (s2n1*t3n1*dtn1 - s1n1*t3n1 - xisqn1*delta)/delta
          an1 = 1. + s0n1 - s2n1*tn1*dtn1 + s1n1*(2.*tn1 - delta)
          
          coeffn1 = bn1-an1*delta-cn1
          #sqrtarg = (bn1-cn1)*(bn1-cn1+2.*an1*delta)
          sqrtarg = tf.square(coeffn1) + 4.*an1*bn1*delta
          sqrtarg = tf.Print(sqrtarg,[sqrtarg],message="sqrtarg",summarize=10000)
          #sqrtarg = tf.maximum(sqrtarg, tf.zeros_like(sqrtarg))
          tn1 = 0.5*(-coeffn1 - tf.sqrt(sqrtarg))/an1
          #tn1 = 0.5*(-coeffn1 + tf.sqrt(tf.square(coeffn1) + 4.*an1*bn1*delta))/an1

          #bn = -s1n*t2n - xisqn
          #an = 1. + s0n + s1n*tn
          #tn = -bn/an
          #this is from the BNS1 method
          #psin = s0n - xisqn/tn
          #psiprimen = s1n + xisqn/t2n
          #tn = tn + (1. + psin)*psin/psiprimen
          
          tn = tn + (tn + s0n*tn - xisqn)*(s0n*tn - xisqn)/(s1n*t2n + xisqn)
          tn = tf.reshape(tn,[-1])
          
          
          
          #tn = tf.Print(tn, [s0n],message="s0n")
          #tn = tf.Print(tn, [s1n],message="s1n")
          #tn = tf.Print(tn, [xisqn],message="xisqn")
          ##tn = tf.Print(tn, [bn],message="bn")
          ##tn = tf.Print(tn, [an],message="an")
          #tn = tf.Print(tn, [tn],message="tn")
          
          told = t
          t = tf.concat([tn1,tn],axis=0)
          
          
          #t = tf.Print(t,[delta],message="delta",summarize=10000)
          #t = tf.Print(t,[frden],message="frden",summarize=10000)
          ##t = tf.Print(t,[f],message="f",summarize=10000)
          ##t = tf.Print(t,[fp],message="fp",summarize=10000)
          ##t = tf.Print(t,[fpp],message="fpp",summarize=10000)
          #t = tf.Print(t,[cn1],message="cn1",summarize=10000)
          #t = tf.Print(t,[bn1],message="bn1",summarize=10000)
          #t = tf.Print(t,[an1],message="an1",summarize=10000)
          #t = tf.Print(t,[sqrtarg],message="sqrtarg",summarize=10000)
          t = tf.Print(t,[t],message="t",summarize=10000)
                    
          #when individual eigenvalues have converged we mark them as such
          #but simply keep iterating on the full vector, since any efficiency
          #gains from partially stopping and chopping up the vector would likely incur
          #more overhead, especially on GPU
          tadvancing = t > told
          unconverged = unconverged & tadvancing
                                 


          #t = tf.Print(t,[psi],message="psi",summarize=10000)
          #t = tf.Print(t,[phi],message="phi",summarize=10000)
          #t = tf.Print(t,[psiprime],message="psiprime",summarize=10000)
          #t = tf.Print(t,[phiprime],message="phiprime",summarize=10000)
          #t = tf.Print(t,[a],message="a",summarize=10000)
          #t = tf.Print(t,[b],message="b",summarize=10000)
          #t = tf.Print(t,[c],message="c",summarize=10000)
          #t = tf.Print(t,[t],message="t",summarize=10000)
          #t = tf.Print(t,[w],message="w",summarize=1000)
          #t = tf.Print(t,[f],message="f",summarize=10000)
          t = tf.Print(t,[magw],message="magw")
          
          return (t,unconverged,j+1)
          
          
        t,unconverged,j = tf.while_loop(cond, body, loop_vars, parallel_iterations=1, back_prop=False)
        
        deltae2 = rho*t
        
        #now compute eigenvectors          
        ei = tf.reshape(unique,[-1,1])
        ej = tf.reshape(unique,[1,-1])
        D = (ej-ei)/rho - tf.reshape(t,[-1,1])
        
        Dinv = tf.reciprocal(D)
        Dinvz = Dinv*tf.reshape(z2,[1,-1])
        #Dinz = tf.where(tf.equal(D,0.),tf.ones_like(Dinvz),Dinvz)
        #Dinvzmag = tf.sqrt(tf.reduce_sum(tf.square(Dinvz),axis=-1))
        Dinvzmag = tf.sqrt(tf.reduce_sum(tf.square(Dinvz),axis=-1,keepdims=True))
        Dinvz = Dinvz/Dinvzmag
        ##Dinvzmag = tf.sqrt(tf.reduce_sum(tf.square(Dinvz),axis=0))
        
        #n.b. this is the most expensive operation (matrix-matrix multiplication to compute the updated eigenvectors)
        UT2out = tf.matmul(Dinvz,UT2)
        
        #UT2out = tf.Print(UT2out,[tf.shape(UT2out)],message="UT2out shape")
        
        #now put everything back together
        #eigenvalues are still guaranteed to be sorted
        eout = estart + tf.scatter_nd(lastidxscol,deltae2,estart.shape)
        UTout = tf.scatter_nd(lastidxscol,UT2out,UTstart.shape) + tf.scatter_nd(nonlastidxscol,UT1,UTstart.shape)
        
        #restore correct order and signs if necessary
        eoutalt = -tf.reverse(eout,axis=(0,))
        UToutalt = tf.reverse(UTout,axis=(0,))
        
        eout = tf.where(flipsign,eoutalt,eout)
        UTout = tf.where(flipsign,UToutalt,UTout)
        
        uout = tf.transpose(UTout)
        
        
        #ufalse = tf.constant(False,shape=uout.shape)
        #umask = tf.logical_or(tf.equal(tf.reshape(t,[1,-1]),0.),umask)
        #umask = tf.equal(estart[1:],estart[:-1])
        #umask = tf.concat([umask,False])
        #umask = tf.reshape(umask,[1,-1])
        #umask = tf.logical_or(umask,ufalse)
        #umask = tf.equal(tf.reshape(eout,[-1,1]),tf.reshape(estart,[1,-1]))
        #uout = tf.where(umask,Uin,uout)
        
        #uout = uout/tf.sqrt(tf.reduce_sum(tf.square(uout),axis=0,keepdims=True))
        #uout = tf.matmul(Uin,Dinvz,transpose_b=True)/Dinvzmag
        #uout = tf.matmul(Uin,Dinvz,transpose_b=True)/tf.transpose(Dinvzmag)
        #uout = tf.where(tf.is_nan(uout),Uin,uout)
        #umag = tf.sqrt(tf.reduce_sum(tf.square(uout),axis=0))
        
        #eout = tf.Print(eout,[eout],message="eout",summarize=1000)
        #uout = tf.Print(uout,[uout],message="uout",summarize=1000)
        #uout = tf.Print(uout,[umag],message="umag",summarize=1000)
        
        eout = tf.Print(eout,[ein],message="ein",summarize=10000)
        eout = tf.Print(eout,[eout],message="eout",summarize=10000)
        
        return (eout,uout)
      
      e,U = tf.cond(dentest, lambda: (ein,Uin), doUpdate)
      
      return (e,U)
    
    esec = self.e
    Usec = self.U
    
    doscaling = tf.constant(False)
    #B,H,doscaling = tf.cond(self.doscaling & self.doiter_old, lambda: doSR1Scaling(B,H,dgrad,dx), lambda: (B,H,self.doscaling))
    esec,Usec = tf.cond(self.doiter_old, lambda: doSR1Update(esec,Usec,dgrad,dx), lambda: (esec,Usec))  
    
    isconvergedxtol = trustradius_out < xtol
    isconvergededmtol = self.predicted_reduction <= 0.
    
    isconverged = self.doiter_old & (isconvergedxtol | isconvergededmtol)
    
    doiter = tf.logical_and(tf.greater(rho,eta),tf.logical_not(isconverged))
    
    
    def build_sol():

      lam = esec
      U = Usec
      
      gradcol = tf.reshape(grad,[-1,1])
      
      #projection of gradient onto eigenvectors
      a = tf.matmul(U, gradcol,transpose_a=True)
      a = tf.reshape(a,[-1])
      
      amagsq = tf.reduce_sum(tf.square(a))
      gmagsq = tf.reduce_sum(tf.square(grad))
      
      asq = tf.square(a)
      
      #deal with null gradient components and repeated eigenvectors

      abarindices = tf.where(asq)
      abarsq = tf.gather(asq,abarindices)
      lambar = tf.gather(lam,abarindices)

      abarsq = tf.reshape(abarsq,[-1])
      lambar = tf.reshape(lambar, [-1])
      
      lambar, abarindicesu = tf.unique(lambar)
      abarsq = tf.unsorted_segment_sum(abarsq,abarindicesu,tf.shape(lambar)[0])
      
      abar = tf.sqrt(abarsq)
       
      #abarsq = tf.Print(abarsq,[asq],message="asq",summarize=1000) 
      #abarsq = tf.Print(abarsq,[lam],message="lam",summarize=1000) 
      #abarsq = tf.Print(abarsq,[abarsq],message="abarsq",summarize=1000)
      #abarsq = tf.Print(abarsq,[lambar],message="lambar",summarize=1000)
       
      e0 = lam[0]
      sigma0 = tf.maximum(-e0,tf.zeros([],dtype=var.dtype))
      
      def phif(s):        
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        pmag = tf.sqrt(pmagsq)
        phipartial = tf.reciprocal(pmag)
        singular = tf.reduce_any(tf.equal(-s,lambar))
        phipartial = tf.where(singular, tf.zeros_like(phipartial), phipartial)
        phi = phipartial - tf.reciprocal(trustradius_out)
        return phi
      
      def phiphiprime(s):
        phi = phif(s)
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        phiprime = tf.pow(pmagsq,-1.5)*tf.reduce_sum(abarsq/tf.pow(lambar+s,3))
        return (phi, phiprime)
        
      
      phisigma0 = phif(sigma0)
      usesolu = tf.logical_and(e0>0. , phisigma0 >= 0.)
      
      def sigma():
        #tol = 1e-10
        maxiter = 50

        sigmainit = tf.reduce_max(tf.abs(a)/trustradius_out - lam)
        sigmainit = tf.maximum(sigmainit,tf.zeros_like(sigmainit))
        phiinit,phiprimeinit = phiphiprime(sigmainit)
                
        loop_vars = [sigmainit, phiinit,phiprimeinit, tf.constant(True), tf.zeros([],dtype=tf.int32)]
        
        def cond(sigma,phi,phiprime,unconverged,j):
          return (unconverged) & (j<maxiter)
        
        def body(sigma,phi,phiprime,unconverged,j):   
          sigmaout = sigma - phi/phiprime
          phiout, phiprimeout = phiphiprime(sigmaout)
          unconverged = (phiout > phi) & (phiout < 0.)
          phiout = tf.Print(phiout,[phiout],message="phiout")
          return (sigmaout,phiout,phiprimeout,unconverged,j+1)
          
        sigmaiter, phiiter,phiprimeiter,unconverged,jiter = tf.while_loop(cond, body, loop_vars, parallel_iterations=1, back_prop=False)        
        return sigmaiter
      
      #sigma=0 corresponds to the unconstrained solution on the interior of the trust region
      sigma = tf.cond(usesolu, lambda: tf.zeros([],dtype=var.dtype), sigma)

      #solution can be computed directly from eigenvalues and eigenvectors
      coeffs = -a/(lam+sigma)
      coeffs = tf.reshape(coeffs,[1,-1])
      #p = tf.reduce_sum(coeffs*U, axis=-1)
      p = tf.matmul(U,tf.reshape(coeffs,[-1,1]))
      p = tf.reshape(p,[-1])

      Umag = tf.sqrt(tf.reduce_sum(tf.square(U),axis=0))
      coeffsmag = tf.sqrt(tf.reduce_sum(tf.square(coeffs)))
      pmag = tf.sqrt(tf.reduce_sum(tf.square(p)))
      p = tf.Print(p,[Umag],message="Umag",summarize=10000)
      p = tf.Print(p,[pmag,coeffsmag,sigma],message="pmag,coeffsmag,sigma")

      #predicted reduction also computed directly from eigenvalues and eigenvectors
      predicted_reduction_out = -(tf.reduce_sum(a*coeffs) + 0.5*tf.reduce_sum(lam*tf.square(coeffs)))
      
      return [var+p, predicted_reduction_out, tf.logical_not(usesolu), grad]

    doiter = tf.Print(doiter,[doiter],message="doiter")
    loopout = tf.cond(doiter, lambda: build_sol(), lambda: [self.var_old+0., tf.zeros_like(loss),tf.constant(False),self.grad_old])
    var_out, predicted_reduction_out, atboundary_out, grad_out = loopout
    
    alist = []
    
    with tf.control_dependencies(loopout):
      oldvarassign = tf.assign(self.var_old,var)
      alist.append(oldvarassign)
      alist.append(tf.assign(self.loss_old,loss))
      alist.append(tf.assign(self.doiter_old, doiter))
      alist.append(tf.assign(self.doscaling,doscaling))
      alist.append(tf.assign(self.grad_old,grad_out))
      alist.append(tf.assign(self.predicted_reduction,predicted_reduction_out))
      alist.append(tf.assign(self.atboundary_old, atboundary_out))
      alist.append(tf.assign(self.trustradius, trustradius_out))
      alist.append(tf.assign(self.isfirstiter,False)) 
      alist.append(tf.assign(self.e,esec)) 
      alist.append(tf.assign(self.U,Usec)) 
       
    clist = []
    clist.extend(loopout)
    clist.append(oldvarassign)
    with tf.control_dependencies(clist):
      varassign = tf.assign(var, var_out)
      
      alist.append(varassign)
      return [isconverged,tf.group(alist)]





class SR1TrustOBS:
    
  def __init__(self, loss, var,grad, initialtrustradius = 1.):
    
    self.trustradius = tf.Variable(initialtrustradius*tf.ones_like(loss),trainable=False)
    self.loss_old = tf.Variable(tf.zeros_like(loss), trainable=False)
    self.predicted_reduction = tf.Variable(tf.zeros_like(loss), trainable = False)
    self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    self.atboundary_old = tf.Variable(False, trainable=False)
    self.doiter_old = tf.Variable(False, trainable = False)
    self.grad_old = tf.Variable(tf.zeros_like(var), trainable=False)
    self.isfirstiter = tf.Variable(True, trainable=False)
    self.B = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    self.H = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    self.doscaling = tf.Variable(False)
    
  def initialize(self, loss, var, grad, B = None, H = None):
    alist = []
    alist.append(tf.assign(self.var_old,var))
    alist.append(tf.assign(self.grad_old,grad))
    
    if B is not None and H is not None:
      alist.append(tf.assign(self.B,B))
      alist.append(tf.assign(self.H,H))
    return tf.group(alist)
  
    
  #def initialize(self, loss, var, k=7, initialtrustradius = 1.):
    #self.k = k
    
    #self.trustradius = tf.Variable(initialtrustradius*tf.ones_like(loss),trainable=False)
    #self.loss_old = tf.Variable(tf.zeros_like(loss), trainable=False)
    #self.predicted_reduction = tf.Variable(tf.zeros_like(loss), trainable = False)
    #self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    #self.atboundary_old = tf.Variable(False, trainable=False)
    #self.doiter_old = tf.Variable(True, trainable = False)
    #self.grad_old = tf.Variable(tf.zeros_like(var), trainable=False)
    #self.isfirstiter = tf.Variable(True, trainable=False)
    ##self.B = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    ##self.H = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    #self.ST = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False)
    #self.YT = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False)
    #self.psi = tf.Variable(tf.zeros([var.shape[0],0],dtype=var.dtype),trainable = False)
    #self.M = tf.Variable(tf.zeros([0,0],dtype=var.dtype),trainable = False)
    ##self.gamma = tf.constant(tf.ones([1],dtype=var.dtype), trainable = False)
    #self.gamma = tf.ones([1],dtype=var.dtype)
    ##self.doscaling = tf.Variable(True)
    #self.updateidx = tf.Variable(tf.zeros([1],dtype=tf.int32),trainable = False)
    #self.grad = tf.gradients(loss,var, gate_gradients=True)[0]
        
    #alist = []
    #alist.append(tf.assign(self.trustradius,initialtrustradius))
    #alist.append(tf.assign(self.loss_old, loss))
    #alist.append(tf.assign(self.predicted_reduction, 0.))
    #alist.append(tf.assign(self.var_old, var))
    #alist.append(tf.assign(self.atboundary_old,False))
    #alist.append(tf.assign(self.doiter_old,False))
    #alist.append(tf.assign(self.isfirstiter,True))
    #alist.append(tf.assign(self.ST,tf.zeros_like(self.ST))
    #alist.append(tf.assign(self.YT,tf.zeros_like(self.YT))
    ##alist.append(tf.assign(self.doscaling,True))
    #alist.append(tf.assign(self.grad_old,self.grad))
    
    ##if doScaling
    

    #return tf.group(alist)

  
  def minimize(self, loss, var, grad = None):
    
    if grad is None:
      grad = tf.gradients(loss,var, gate_gradients=True)[0]
    
    
    xtol = np.finfo(var.dtype.as_numpy_dtype).eps
    #edmtol = math.sqrt(xtol)
    #edmtol = xtol
    #edmtol = 1e-8
    #edmtol = 0.
    #eta = 0.
    eta = 0.15
    
          
    actual_reduction = self.loss_old - loss
    
    #actual_reduction = tf.Print(actual_reduction,[self.loss_old, loss, actual_reduction])
    isnull = tf.logical_not(self.doiter_old)
    rho = actual_reduction/self.predicted_reduction
    rho = tf.where(tf.is_nan(loss), tf.zeros_like(loss), rho)
    rho = tf.where(isnull, tf.ones_like(loss), rho)
  
    dgrad = grad - self.grad_old
    dx = var - self.var_old
    dxmag = tf.sqrt(tf.reduce_sum(tf.square(dx)))
  
    trustradius_out = tf.where(tf.less(rho,0.25),0.25*self.trustradius,tf.where(tf.logical_and(tf.greater(rho,0.75),self.atboundary_old),2.*self.trustradius, self.trustradius))
    #trustradius_out = tf.minimum(trustradius_out,1e10)
    
    #trustradius_out = tf.where(tf.less(rho,0.1),0.5*self.trustradius,
                               #tf.where(tf.less(rho,0.75), self.trustradius,
                               #tf.where(tf.less_equal(dxmag,0.8*self.trustradius), self.trustradius,
                               #2.*self.trustradius)))
                               
    trustradius_out = tf.where(self.doiter_old, trustradius_out, self.trustradius)

    
    trustradius_out = tf.Print(trustradius_out, [actual_reduction,self.predicted_reduction,rho, trustradius_out], message = "actual_reduction, self.predicted_reduction, rho, trustradius_out: ")
    
    #def hesspexact(v):
      #return tf.gradients(self.grad*tf.stop_gradient(v),var, gate_gradients=True)[0]    
    
    #def hesspapprox(B,v):
      #return tf.reshape(tf.matmul(B,tf.reshape(v,[-1,1])),[-1])    
    
    #def Bv(gamma,psi,M,vcol):
      #return gamma*vcol + tf.matmul(psi,tf.matmul(M,tf.matmul(psi,vcol,transpose_a=True)))
    
    #def Bvflat(gamma,psi,M,v):
      #vcol = tf.reshape(v,[-1,1])
      #return tf.reshape(Bv(gamma,psi,M,vcol),[-1])
      
    def Bv(gamma,psi,MpsiT,vcol):
      return gamma*vcol + tf.matmul(psi,tf.matmul(MpsiT,vcol))
    
    def Bvflat(gamma,psi,MpsiT,v):
      vcol = tf.reshape(v,[-1,1])
      return tf.reshape(Bv(gamma,psi,MpsiT,vcol),[-1])
    
    def hesspexact(v):
      return tf.gradients(self.grad*tf.stop_gradient(v),var, gate_gradients=True)[0]    
    
    def hesspapprox(B,v):
      return tf.reshape(tf.matmul(B,tf.reshape(v,[-1,1])),[-1])    
    
    def doSR1Scaling(Bin,Hin,yin,dxin):
      s_norm2 = tf.reduce_sum(tf.square(dxin))
      y_norm2 = tf.reduce_sum(tf.square(yin))
      ys = tf.abs(tf.reduce_sum(yin*dxin))
      invalid = tf.equal(ys,0.) | tf.equal(y_norm2, 0.) | tf.equal(s_norm2, 0.)
      scale = tf.where(invalid, tf.ones_like(ys), y_norm2/ys)
      scale = tf.Print(scale,[scale],message = "doing sr1 scaling")
      B = scale*Bin
      H = Hin/scale
      return (B,H,tf.constant(False))
    
    def doSR1Update(Bin,Hin,yin,dxin):
      y = tf.reshape(yin,[-1,1])
      dx = tf.reshape(dxin,[-1,1])
      Bx = tf.matmul(Bin,dx)
      dyBx = y - Bx
      den = tf.matmul(dyBx,dx,transpose_a=True)
      deltaB = tf.matmul(dyBx,dyBx,transpose_b=True)/den
      dennorm = tf.sqrt(tf.reduce_sum(tf.square(dx)))*tf.sqrt(tf.reduce_sum(tf.square(dyBx)))
      dentest = tf.less(tf.abs(den),1e-8*dennorm)
      dentest = tf.reshape(dentest,[])
      dentest = tf.logical_or(dentest,tf.equal(actual_reduction,0.))
      deltaB = tf.where(dentest,tf.zeros_like(deltaB),deltaB)
      #deltaB = tf.where(self.doiter_old, deltaB, tf.zeros_like(deltaB))
      
      Hy = tf.matmul(Hin,y)
      dxHy = dx - Hy
      deltaH = tf.matmul(dxHy,dxHy,transpose_b=True)/tf.matmul(dxHy,y,transpose_a=True)
      deltaH = tf.where(dentest,tf.zeros_like(deltaH),deltaH)
      #deltaH = tf.where(self.doiter_old, deltaH, tf.zeros_like(deltaH))
      
      B = Bin + deltaB
      H = Hin + deltaH
      return (B,H)
    
    #grad = self.grad
    B = self.B
    H = self.H
    
    #dgrad = grad - self.grad_old
    #dx = var - self.var_old
    doscaling = tf.constant(False)
    #B,H,doscaling = tf.cond(self.doscaling & self.doiter_old, lambda: doSR1Scaling(B,H,dgrad,dx), lambda: (B,H,self.doscaling))
    B,H = tf.cond(self.doiter_old, lambda: doSR1Update(B,H,dgrad,dx), lambda: (B,H))  
    
  
    
    #psi = tf.Print(psi,[psi],message="psi: ")
    #M = tf.Print(M,[M],message="M: ")
    
    isconvergedxtol = trustradius_out < xtol
    #isconvergededmtol = tf.logical_not(self.isfirstiter) & (self.predicted_reduction <= 0.)
    isconvergededmtol = self.predicted_reduction <= 0.
    
    isconverged = self.doiter_old & (isconvergedxtol | isconvergededmtol)
    
    doiter = tf.logical_and(tf.greater(rho,eta),tf.logical_not(isconverged))
    
    #doiter = tf.Print(doiter, [doiter, isconvergedxtol, isconvergededmtol,isconverged,trustradius_out])
    
    def build_sol():

      lam,U = tf.self_adjoint_eig(B) #TODO: check if what is returned here should actually be UT in the paper
      #U = tf.transpose(U)

      
      #R = tf.Print(R,[detR],message = "detR")

      
      #Rinverse = tf.matrix_inverse(R)
      
      gradcol = tf.reshape(grad,[-1,1])
      
      a = tf.matmul(U, gradcol,transpose_a=True)
      a = tf.reshape(a,[-1])
      
      amagsq = tf.reduce_sum(tf.square(a))
      gmagsq = tf.reduce_sum(tf.square(grad))
      
      a = tf.Print(a,[amagsq,gmagsq],message = "amagsq,gmagsq")
      
      #a = tf.matmul(U, gradcol,transpose_a=False)
      asq = tf.square(a)
      

      abarindices = tf.where(asq)
      abarsq = tf.gather(asq,abarindices)
      lambar = tf.gather(lam,abarindices)

      abarsq = tf.reshape(abarsq,[-1])
      lambar = tf.reshape(lambar, [-1])
      
      lambar, abarindicesu = tf.unique(lambar)
      abarsq = tf.unsorted_segment_sum(abarsq,abarindicesu,tf.shape(lambar)[0])
      
      abar = tf.sqrt(abarsq)
      
      #abarsq = tf.square(abar)


      #nv = tf.shape(ST)[0]
      #I = tf.eye(int(var.shape[0]),dtype=var.dtype)
      #B = gamma*I + tf.matmul(psi,tf.matmul(M,psi,transpose_b=True))
      #B = tf.Print(B, [B],message="B: ", summarize=1000)
      #efull = tf.self_adjoint_eigvals(B)
      #lam = efull[:1+nv]
      
      e0 = lam[0]
      sigma0 = tf.maximum(-e0,tf.zeros([],dtype=var.dtype))
      
      
      #lambar, lamidxs = tf.unique(lam)
      #abarsq = tf.segment_sum(asq,lamidxs)
      
      abarsq = tf.Print(abarsq, [a, abar, lam, lambar], message = "a,abar,lam,lambar")
      
      
      def phif(s):        
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        pmag = tf.sqrt(pmagsq)
        phipartial = tf.reciprocal(pmag)
        singular = tf.reduce_any(tf.equal(-s,lambar))
        #singular = tf.logical_or(singular, tf.is_nan(phipartial))
        #singular = tf.logical_or(singular, tf.is_inf(phipartial))
        phipartial = tf.where(singular, tf.zeros_like(phipartial), phipartial)
        phi = phipartial - tf.reciprocal(trustradius_out)
        return phi
      
      def phiphiprime(s):
        phi = phif(s)
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        phiprime = tf.pow(pmagsq,-1.5)*tf.reduce_sum(abarsq/tf.pow(lambar+s,3))
        return (phi, phiprime)
        
      
      phisigma0 = phif(sigma0)
      #usesolu = e0>0. & phisigma0 >= 0.
      usesolu = tf.logical_and(e0>0. , phisigma0 >= 0.)
      usesolu = tf.Print(usesolu,[sigma0,phisigma0,usesolu], message = "sigma0, phisigma0,usesolu: ")

      def solu():
        return -tf.matmul(H,gradcol)
      
      def sol():
        tol = 1e-8
        maxiter = 50

        sigmainit = tf.reduce_max(tf.abs(a)/trustradius_out - lam)
        sigmainit = tf.maximum(sigmainit,tf.zeros_like(sigmainit))
        phiinit = phif(sigmainit)
        
        sigmainit = tf.Print(sigmainit,[sigmainit,phiinit],message = "sigmainit, phinit: ")

        
        loop_vars = [sigmainit, phiinit, tf.zeros([],dtype=tf.int32)]
        
        def cond(sigma,phi,j):
          #phi = tf.Print(phi,[phi],message = "checking phi in cond()")
          return tf.logical_and(phi < -tol, j<maxiter)
          #return tf.logical_and(tf.abs(phi) > tol, j<maxiter)
        
        def body(sigma,phi,j):   
          #sigma = tf.Print(sigma, [sigma, phi], message = "sigmain, phiin: ")
          phiout, phiprimeout = phiphiprime(sigma)
          sigmaout = sigma - phiout/phiprimeout
          sigmaout = tf.Print(sigmaout, [sigmaout,phiout, phiprimeout], message = "sigmaout, phiout, phiprimeout: ")
          return (sigmaout,phiout,j+1)
          
        sigmaiter, phiiter, jiter = tf.while_loop(cond, body, loop_vars, parallel_iterations=1, back_prop=False)
        #sigmaiter = tf.Print(sigmaiter,[sigmaiter,phiiter],message = "sigmaiter,phiiter")
        
        coeffs = -a/(lam+sigmaiter)
        coeffs = tf.reshape(coeffs,[1,-1])
        p = tf.reduce_sum(coeffs*U, axis=-1)
        
        return p
      
      p = tf.cond(usesolu, solu, sol)
      p = tf.reshape(p,[-1])
      
      magp = tf.sqrt(tf.reduce_sum(tf.square(p)))
      p = tf.Print(p,[magp],message = "magp")

      #e0val = efull[0]
      #e0val = e0
      
      #Bfull = tau*I + tf.matmul(psi,tf.matmul(M,psi,transpose_b=True))
      #pfull = -tf.matrix_solve(Bfull,tf.reshape(grad,[-1,1]))
      #pfull = tf.reshape(p,[-1])
      #p = pfull

      #p  = tf.Print(p,[e0,e0val,sigma0,sigma,tau], message = "e0, e0val, sigma0, sigma, tau")
      #p  = tf.Print(p,[lam,efull], message = "lam, efull")

      predicted_reduction_out = -(tf.reduce_sum(grad*p) + 0.5*tf.reduce_sum(tf.reshape(tf.matmul(B,tf.reshape(p,[-1,1])),[-1])*p) )
      
      return [var+p, predicted_reduction_out, tf.logical_not(usesolu), grad]

    loopout = tf.cond(doiter, lambda: build_sol(), lambda: [self.var_old+0., tf.zeros_like(loss),tf.constant(False),self.grad_old])
    var_out, predicted_reduction_out, atboundary_out, grad_out = loopout
        
    #var_out = tf.Print(var_out,[],message="var_out")
    #loopout[0] = var_out
    
    alist = []
    
    with tf.control_dependencies(loopout):
      oldvarassign = tf.assign(self.var_old,var)
      alist.append(oldvarassign)
      alist.append(tf.assign(self.loss_old,loss))
      alist.append(tf.assign(self.doiter_old, doiter))
      alist.append(tf.assign(self.B,B))
      alist.append(tf.assign(self.H,H))
      alist.append(tf.assign(self.doscaling,doscaling))
      alist.append(tf.assign(self.grad_old,grad_out))
      alist.append(tf.assign(self.predicted_reduction,predicted_reduction_out))
      alist.append(tf.assign(self.atboundary_old, atboundary_out))
      alist.append(tf.assign(self.trustradius, trustradius_out))
      alist.append(tf.assign(self.isfirstiter,False)) 
       
    clist = []
    clist.extend(loopout)
    clist.append(oldvarassign)
    with tf.control_dependencies(clist):
      varassign = tf.assign(var, var_out)
      #varassign = tf.Print(varassign,[],message="varassign")
      
      alist.append(varassign)
      return [isconverged,tf.group(alist)]




class LSR1TrustOBS:
    
  def __init__(self, loss, var,grad, k=100, initialtrustradius = 1.):
    self.k = k
    
    self.trustradius = tf.Variable(initialtrustradius*tf.ones_like(loss),trainable=False)
    self.loss_old = tf.Variable(tf.zeros_like(loss), trainable=False)
    self.predicted_reduction = tf.Variable(tf.zeros_like(loss), trainable = False)
    #self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    self.atboundary_old = tf.Variable(False, trainable=False)
    self.doiter_old = tf.Variable(False, trainable = False)
    self.grad_old = tf.Variable(tf.zeros_like(var), trainable=False)
    self.isfirstiter = tf.Variable(True, trainable=False)
    #self.B = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    #self.H = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    self.ST = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False, validate_shape=False)
    self.YT = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False, validate_shape=False)
    self.psi = tf.Variable(tf.zeros([var.shape[0],0],dtype=var.dtype),trainable = False, validate_shape=False)
    self.M = tf.Variable(tf.zeros([0,0],dtype=var.dtype),trainable = False, validate_shape=False)
    self.MpsiT = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype),trainable = False, validate_shape=False)
    #self.gamma = tf.constant(tf.ones([1],dtype=var.dtype), trainable = False)
    #self.gamma = tf.ones([],dtype=var.dtype)
    self.gamma = tf.Variable(tf.ones([],dtype=var.dtype),trainable=False)
    self.doscaling = tf.Variable(False)
    self.updateidx = tf.Variable(tf.zeros([],dtype=tf.int32),trainable = False)
    #self.grad = tf.gradients(loss,var, gate_gradients=True)[0]
    
  def initialize(self, loss, var, grad):
    alist = []
    alist.append(tf.assign(self.var_old,var))
    alist.append(tf.assign(self.grad_old,grad))
    
    return tf.group(alist)
    
  #def initialize(self, loss, var, k=7, initialtrustradius = 1.):
    #self.k = k
    
    #self.trustradius = tf.Variable(initialtrustradius*tf.ones_like(loss),trainable=False)
    #self.loss_old = tf.Variable(tf.zeros_like(loss), trainable=False)
    #self.predicted_reduction = tf.Variable(tf.zeros_like(loss), trainable = False)
    #self.var_old = tf.Variable(tf.zeros_like(var),trainable=False)
    #self.atboundary_old = tf.Variable(False, trainable=False)
    #self.doiter_old = tf.Variable(True, trainable = False)
    #self.grad_old = tf.Variable(tf.zeros_like(var), trainable=False)
    #self.isfirstiter = tf.Variable(True, trainable=False)
    ##self.B = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    ##self.H = tf.Variable(tf.eye(int(var.shape[0]),dtype=var.dtype),trainable=False)
    #self.ST = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False)
    #self.YT = tf.Variable(tf.zeros([0,var.shape[0]],dtype=var.dtype), trainable = False)
    #self.psi = tf.Variable(tf.zeros([var.shape[0],0],dtype=var.dtype),trainable = False)
    #self.M = tf.Variable(tf.zeros([0,0],dtype=var.dtype),trainable = False)
    ##self.gamma = tf.constant(tf.ones([1],dtype=var.dtype), trainable = False)
    #self.gamma = tf.ones([1],dtype=var.dtype)
    ##self.doscaling = tf.Variable(True)
    #self.updateidx = tf.Variable(tf.zeros([1],dtype=tf.int32),trainable = False)
    #self.grad = tf.gradients(loss,var, gate_gradients=True)[0]
        
    #alist = []
    #alist.append(tf.assign(self.trustradius,initialtrustradius))
    #alist.append(tf.assign(self.loss_old, loss))
    #alist.append(tf.assign(self.predicted_reduction, 0.))
    #alist.append(tf.assign(self.var_old, var))
    #alist.append(tf.assign(self.atboundary_old,False))
    #alist.append(tf.assign(self.doiter_old,False))
    #alist.append(tf.assign(self.isfirstiter,True))
    #alist.append(tf.assign(self.ST,tf.zeros_like(self.ST))
    #alist.append(tf.assign(self.YT,tf.zeros_like(self.YT))
    ##alist.append(tf.assign(self.doscaling,True))
    #alist.append(tf.assign(self.grad_old,self.grad))
    
    ##if doScaling
    

    #return tf.group(alist)

  
  def minimize(self, loss, var, grad = None):
    
    if grad is None:
      grad = tf.gradients(loss,var, gate_gradients=True)[0]
    
    
    xtol = np.finfo(var.dtype.as_numpy_dtype).eps
    #edmtol = math.sqrt(xtol)
    #edmtol = xtol
    #edmtol = 1e-8
    #edmtol = 0.
    eta = 0.
    #eta = 0.15
    #tau1 = 0.1
    #tau2 = 0.3

    #defaults from nocedal and wright
    #eta = 1e-3
    tau1 = 0.1
    tau2 = 0.75
          
    actual_reduction = self.loss_old - loss
    
    #actual_reduction = tf.Print(actual_reduction,[self.loss_old, loss, actual_reduction])
    isnull = tf.logical_not(self.doiter_old)
    rho = actual_reduction/self.predicted_reduction
    rho = tf.where(tf.is_nan(loss), tf.zeros_like(loss), rho)
    rho = tf.where(isnull, tf.ones_like(loss), rho)
  
    dgrad = grad - self.grad_old
    dx = var - self.var_old
    dxmag = tf.sqrt(tf.reduce_sum(tf.square(dx)))
  
    #trustradius_out = tf.where(tf.less(rho,0.25),0.25*self.trustradius,tf.where(tf.logical_and(tf.greater(rho,0.75),self.atboundary_old),2.*self.trustradius, self.trustradius))
    #trustradius_out = tf.minimum(trustradius_out,1e10)
    
    trustradius_out = tf.where(tf.less(rho,tau1),0.5*self.trustradius,
                               tf.where(tf.less(rho,tau2), self.trustradius,
                               tf.where(tf.less_equal(dxmag,0.8*self.trustradius), self.trustradius,
                               2.*self.trustradius)))
                               
    trustradius_out = tf.where(self.doiter_old, trustradius_out, self.trustradius)

    
    trustradius_out = tf.Print(trustradius_out, [actual_reduction,self.predicted_reduction,rho, trustradius_out], message = "actual_reduction, self.predicted_reduction, rho, trustradius_out: ")
    
    #def hesspexact(v):
      #return tf.gradients(self.grad*tf.stop_gradient(v),var, gate_gradients=True)[0]    
    
    #def hesspapprox(B,v):
      #return tf.reshape(tf.matmul(B,tf.reshape(v,[-1,1])),[-1])    
    
    #def Bv(gamma,psi,M,vcol):
      #return gamma*vcol + tf.matmul(psi,tf.matmul(M,tf.matmul(psi,vcol,transpose_a=True)))
    
    #def Bvflat(gamma,psi,M,v):
      #vcol = tf.reshape(v,[-1,1])
      #return tf.reshape(Bv(gamma,psi,M,vcol),[-1])
      
    def Bv(gamma,psi,MpsiT,vcol):
      return gamma*vcol + tf.matmul(psi,tf.matmul(MpsiT,vcol))
    
    def Bvflat(gamma,psi,MpsiT,v):
      vcol = tf.reshape(v,[-1,1])
      return tf.reshape(Bv(gamma,psi,MpsiT,vcol),[-1])
    
    
    
    def doSR1Scaling(yin,dxin):
      s_norm2 = tf.reduce_sum(tf.square(dxin))
      y_norm2 = tf.reduce_sum(tf.square(yin))
      ys = tf.abs(tf.reduce_sum(yin*dxin))
      invalid = tf.equal(ys,0.) | tf.equal(y_norm2, 0.) | tf.equal(s_norm2, 0.)
      scale = tf.where(invalid, tf.ones_like(ys), y_norm2/ys)
      scale = tf.Print(scale,[scale],message = "doing sr1 scaling")
      return (scale,False)
    
    gamma,doscaling = tf.cond(self.doscaling & self.doiter_old, lambda: doSR1Scaling(dgrad,dx), lambda: (self.gamma,self.doscaling))

    
    def doSR1Update(STin,YTin,yin,dxin):
      ycol = tf.reshape(yin,[-1,1])
      dxcol = tf.reshape(dxin,[-1,1])
      
      yrow = tf.reshape(yin,[1,-1])
      dxrow = tf.reshape(dxin,[1,-1])
      
      #dyBx = ycol - Bv(gamma,self.psi,self.M,dxcol)
      dyBx = ycol - Bv(gamma,self.psi,self.MpsiT,dxcol)
      den = tf.matmul(dyBx, dxcol, transpose_a = True)
      #den = tf.reshape(den,[])
      
      dennorm = tf.sqrt(tf.reduce_sum(tf.square(dx)))*tf.sqrt(tf.reduce_sum(tf.square(dyBx)))
      dentest = tf.greater(tf.abs(den),1e-8*dennorm)
      dentest = tf.reshape(dentest,[])
      nonzero = dentest
      #nonzero = tf.logical_and(dentest,tf.not_equal(actual_reduction,0.))
      
      #nonzero = tf.Print(nonzero, [den,dennorm, dentest, nonzero], message = "den, dennorm, dentest, nonzero")
      
      #nonzero = tf.abs(den) > 1e-8
      
      #doappend = tf.logical_and(nonzero, tf.shape(STin)[0] < self.k)
      #doreplace = tf.logical_and(nonzero, tf.shape(STin)[0] >= self.k)
      
      sliceidx = tf.where(tf.shape(STin)[0] < self.k, 0, 1)
      
      #print(den.shape)
      
      def update():
        ST = tf.concat([STin[sliceidx:],dxrow],axis=0)
        YT = tf.concat([YTin[sliceidx:],yrow],axis=0)
        return (ST,YT)
      
      ST,YT = tf.cond(nonzero, update, lambda: (STin, YTin))

      return (ST,YT)
    
    ST = self.ST
    YT = self.YT
        

    #doscaling = tf.constant(False)
    ST,YT = tf.cond(self.doiter_old, lambda: doSR1Update(ST,YT,dgrad,dx), lambda: (ST,YT))    
    
    #compute compact representation
    S = tf.transpose(ST)
    Y = tf.transpose(YT)
    psi = Y - gamma*S
    psiT = tf.transpose(psi)
    STY = tf.matmul(ST,YT,transpose_b=True)
    D = tf.matrix_band_part(STY,0,0)
    L = tf.matrix_band_part(STY,-1,0) - D
    LT = tf.transpose(L)
    STB0S = gamma*tf.matmul(ST,S)
    Minverse = D + L + LT - STB0S
    MpsiT = tf.matrix_solve(Minverse,psiT)
    #M = tf.matrix_inverse(Minverse)
    
    #psi = tf.Print(psi,[psi],message="psi: ")
    #M = tf.Print(M,[M],message="M: ")
    
    isconvergedxtol = trustradius_out < xtol
    #isconvergededmtol = tf.logical_not(self.isfirstiter) & (self.predicted_reduction <= 0.)
    isconvergededmtol = self.predicted_reduction <= 0.
    
    isconverged = self.doiter_old & (isconvergedxtol | isconvergededmtol)
    
    doiter = tf.logical_and(tf.greater(rho,eta),tf.logical_not(isconverged))
    
    #doiter = tf.Print(doiter, [doiter, isconvergedxtol, isconvergededmtol,isconverged,trustradius_out])
    
    def build_sol():
      #grad = self.grad
      
      #compute eigen decomposition
      #psiTpsi = tf.matmul(psiT,psi)
      #epsiTpsi = tf.self_adjoint_eigvals(psiTpsi)
      #e0psiTpsi = tf.reduce_min(epsiTpsi)
      #psiTpsi = tf.Print(psiTpsi,[e0psiTpsi], message = "e0psiTpsi")
      ##psiTpsi = psiTpsi + 4.*tf.maximum(-e0psiTpsi,tf.zeros_like(e0psiTpsi))*tf.eye(tf.shape(psiTpsi)[0],dtype=psiTpsi.dtype)
      
      #RT = tf.cholesky(psiTpsi)
      #R = tf.transpose(RT)
      
      #def chol():
        #RT = tf.cholesky(psiTpsi)
        #R = tf.transpose(RT)
        #return (R,RT)
      
      #def qr():
        #Q,R = tf.qr(psi)
        #RT = tf.transpose(R)
        #return (R,RT)
      
      ##R,RT = tf.cond(e0psiTpsi > 0., chol, qr)
      #R,RT = chol()
      
      #RT = tf.cholesky(psiTpsi)
      #R = tf.transpose(RT)
      
      
      Q,R = tf.qr(psi)
      detR = tf.matrix_determinant(R)
      #assertR = tf.Assert(tf.not_equal(detR,0.),[detR])
      #with tf.control_dependencies([assertR]):
        #R = tf.Print(R,[detR],message="detR")
      
      RT = tf.transpose(R)
      MRT = tf.matrix_solve(Minverse,RT)
      RMRT = tf.matmul(R,MRT)
      #RMRT = tf.matmul(R,tf.matmul(M,R,transpose_b=True))

      e,U = tf.self_adjoint_eig(RMRT) #TODO: check if what is returned here should actually be UT in the paper
      

      
      #R = tf.Print(R,[detR],message = "detR")

      
      #Rinverse = tf.matrix_inverse(R)
      
      gradcol = tf.reshape(grad,[-1,1])
      
      #gpll = tf.matmul(tf.matmul(psi,tf.matmul(Rinverse,U)), gradcol,transpose_a=True)
      #gpll = tf.matmul(tf.matmul(psi,tf.matrix_solve(R,U)), gradcol,transpose_a=True)
      gpll = tf.matmul(tf.matmul(Q,U), gradcol,transpose_a=True)
      gpll = tf.reshape(gpll,[-1])
      gpllsq = tf.square(gpll)
      gmagsq = tf.reduce_sum(tf.square(grad))
      gpllmagsq = tf.reduce_sum(gpllsq)
      gperpmagsq = gmagsq - gpllmagsq
      gperpmagsq = tf.maximum(gperpmagsq,tf.zeros_like(gperpmagsq))
      gperpmagsq = tf.reshape(gperpmagsq,[1])
      
      #gpll = tf.Print(gpll,[tf.shape(gpll)], message = "gpll shape:")
      a = gpll
      a = tf.concat([a,tf.sqrt(gperpmagsq)],axis=0)
      #a = a[:var.shape[0]]
      asq = tf.square(a)
      
      #lam = e + gamma
      #lam = tf.concat([lam,tf.reshape(gamma,[1])],axis=0)
      lam = tf.pad(e,[[0,1]]) + gamma
      #lam = lam[:var.shape[0]]

      abarindices = tf.where(asq)
      abarsq = tf.gather(asq,abarindices)
      lambar = tf.gather(lam,abarindices)

      abarsq = tf.reshape(abarsq,[-1])
      lambar = tf.reshape(lambar, [-1])
      
      lambar, abarindicesu = tf.unique(lambar)
      abarsq = tf.unsorted_segment_sum(abarsq,abarindicesu,tf.shape(lambar)[0])
      
      abar = tf.sqrt(abarsq)
      
      #abarsq = tf.square(abar)


      #nv = tf.shape(ST)[0]
      #I = tf.eye(int(var.shape[0]),dtype=var.dtype)
      #B = gamma*I + tf.matmul(psi,tf.matmul(M,psi,transpose_b=True))
      #B = tf.Print(B, [B],message="B: ", summarize=1000)
      #efull = tf.self_adjoint_eigvals(B)
      #lam = efull[:1+nv]
      
      e0 = tf.minimum(lam[0],gamma)
      sigma0 = tf.maximum(-e0,tf.zeros([],dtype=var.dtype))
      
      
      #lambar, lamidxs = tf.unique(lam)
      #abarsq = tf.segment_sum(asq,lamidxs)
      
      abarsq = tf.Print(abarsq, [a, abar, lam, lambar,gperpmagsq,gmagsq,gpllmagsq], message = "a,abar,lam,lambar,gperpmagsq,gmagsq, gpllmagsq")
      
      
      def phif(s):        
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        pmag = tf.sqrt(pmagsq)
        phipartial = tf.reciprocal(pmag)
        singular = tf.reduce_any(tf.equal(-s,lambar))
        #singular = tf.logical_or(singular, tf.is_nan(phipartial))
        #singular = tf.logical_or(singular, tf.is_inf(phipartial))
        phipartial = tf.where(singular, tf.zeros_like(phipartial), phipartial)
        phi = phipartial - tf.reciprocal(trustradius_out)
        return phi
      
      def phiphiprime(s):
        phi = phif(s)
        pmagsq = tf.reduce_sum(abarsq/tf.square(lambar+s))
        phiprime = tf.pow(pmagsq,-1.5)*tf.reduce_sum(abarsq/tf.pow(lambar+s,3))
        return (phi, phiprime)
        
      
      phisigma0 = phif(sigma0)
      #usesolu = e0>0. & phisigma0 >= 0.
      usesolu = tf.logical_and(e0>0. , phisigma0 >= 0.)
      usesolu = tf.Print(usesolu,[sigma0,phisigma0,usesolu], message = "sigma0, phisigma0,usesolu: ")

      
      def sigma_sol():
        tol = 1e-8
        maxiter = 50

        sigmainit = tf.reduce_max(tf.abs(a)/trustradius_out - lam)
        sigmainit = tf.maximum(sigmainit,tf.zeros_like(sigmainit))
        phiinit = phif(sigmainit)
        
        sigmainit = tf.Print(sigmainit,[sigmainit,phiinit],message = "sigmainit, phinit: ")

        
        loop_vars = [sigmainit, phiinit, tf.zeros([],dtype=tf.int32)]
        
        def cond(sigma,phi,j):
          return tf.logical_and(phi < -tol, j<maxiter)
        
        def body(sigma,phi,j):   
          #sigma = tf.Print(sigma, [sigma, phi], message = "sigmain, phiin: ")
          phiout, phiprimeout = phiphiprime(sigma)
          sigmaout = sigma - phiout/phiprimeout
          sigmaout = tf.Print(sigmaout, [sigmaout,phiout, phiprimeout], message = "sigmaout, phiout, phiprimeout: ")
          return (sigmaout,phiout,j+1)
          
        sigmaiter, phiiter, jiter = tf.while_loop(cond, body, loop_vars, parallel_iterations=1, back_prop=False)
        
        return sigmaiter
      
      sigma = tf.cond(usesolu, lambda: sigma0, lambda: sigma_sol())
      tau = sigma + gamma
      
      #print(var.shape[0])
      I = tf.eye(int(var.shape[0]),dtype=var.dtype)
      innerinverse = tau*Minverse + tf.matmul(psi,psi,transpose_a=True)
      innerpsiT = tf.matrix_solve(innerinverse,psiT)
      #inner = tf.matrix_inverse(innerinverse)
      #inner2 = tf.matmul(tf.matmul(psi,inner),psi, transpose_b=True)
      inner2 = tf.matmul(psi,innerpsiT)
      p = -tf.matmul(I-inner2, gradcol)/tau
      p = tf.reshape(p,[-1])
      
      magp = tf.sqrt(tf.reduce_sum(tf.square(p)))
      detMinverse = tf.matrix_determinant(Minverse)
      detinnerinverse = tf.matrix_determinant(innerinverse)
      p = tf.Print(p,[magp,detR,detMinverse,detinnerinverse],message = "magp, detR, detMinverse, detinnerinverse")

      #e0val = efull[0]
      #e0val = e0
      
      #Bfull = tau*I + tf.matmul(psi,tf.matmul(M,psi,transpose_b=True))
      #pfull = -tf.matrix_solve(Bfull,tf.reshape(grad,[-1,1]))
      #pfull = tf.reshape(p,[-1])
      #p = pfull

      #p  = tf.Print(p,[e0,e0val,sigma0,sigma,tau], message = "e0, e0val, sigma0, sigma, tau")
      #p  = tf.Print(p,[lam,efull], message = "lam, efull")

      predicted_reduction_out = -(tf.reduce_sum(grad*p) + 0.5*tf.reduce_sum(Bvflat(gamma, psi, MpsiT, p)*p))
      
      return [var+p, predicted_reduction_out, tf.logical_not(usesolu), grad]

    loopout = tf.cond(doiter, lambda: build_sol(), lambda: [self.var_old+0., tf.zeros_like(loss),tf.constant(False),self.grad_old])
    var_out, predicted_reduction_out, atboundary_out, grad_out = loopout
        
    #var_out = tf.Print(var_out,[],message="var_out")
    #loopout[0] = var_out
    
    alist = []
    
    with tf.control_dependencies(loopout):
      oldvarassign = tf.assign(self.var_old,var)
      alist.append(oldvarassign)
      alist.append(tf.assign(self.loss_old,loss))
      alist.append(tf.assign(self.doiter_old, doiter))
      alist.append(tf.assign(self.ST,ST,validate_shape=False))
      alist.append(tf.assign(self.YT,YT,validate_shape=False))
      alist.append(tf.assign(self.psi,psi,validate_shape=False))
      #alist.append(tf.assign(self.M,M,validate_shape=False))
      alist.append(tf.assign(self.MpsiT,MpsiT,validate_shape=False))
      alist.append(tf.assign(self.doscaling,doscaling))
      alist.append(tf.assign(self.grad_old,grad_out))
      alist.append(tf.assign(self.predicted_reduction,predicted_reduction_out))
      alist.append(tf.assign(self.atboundary_old, atboundary_out))
      alist.append(tf.assign(self.trustradius, trustradius_out))
      alist.append(tf.assign(self.isfirstiter,False)) 
      alist.append(tf.assign(self.gamma,gamma)) 
       
    clist = []
    clist.extend(loopout)
    clist.append(oldvarassign)
    with tf.control_dependencies(clist):
      varassign = tf.assign(var, var_out)
      #varassign = tf.Print(varassign,[],message="varassign")
      
      alist.append(varassign)
      return [isconverged,tf.group(alist)]

